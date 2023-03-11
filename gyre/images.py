# Utility functions for handling images as PyTorch Tensors

# All images in are in BCHW unless specified in the variable name, as floating point 0..1
# All functions will handle RGB or RGBA images

from math import ceil, sqrt
from typing import Literal

import cv2 as cv
import numpy as np
import PIL
import torch
import torchvision

from .resize_right import interp_methods
from .resize_right import resize as resize_right


def fromPIL(image):
    # Get as numpy HWC 0..1
    rgbHWC = np.array(image).astype(np.float32) / 255.0
    # Convert to BCHW
    rgbBCHW = rgbHWC[None].transpose(0, 3, 1, 2)
    # And convert to Tensor
    return torch.from_numpy(rgbBCHW)


def toPIL(tensor):
    # Convert to BCHW if just CHW
    if tensor.ndim == 3:
        tensor = tensor[None, ...]
    # Then convert to BHWC
    rgbBHWC = tensor.permute(0, 2, 3, 1)
    # Then convert from 0..1 to 0..255
    images = (rgbBHWC.to(torch.float32) * 255).round().to(torch.uint8).cpu().numpy()
    # And put into PIL image instances
    return [PIL.Image.fromarray(image) for image in images]


def fromCV(bgrHWC):
    # Handle mono (either no channels at all, or exactly one channel)
    if bgrHWC.ndim == 2 or bgrHWC.shape[-1] == 1:
        tensor = torch.from_numpy(bgrHWC).to(torch.float32) / 255.0
        while tensor.ndim < 4:
            tensor = tensor.unsqueeze(dim=0)
        return tensor

    # Handle color
    else:
        bgrBCHW = bgrHWC[None].transpose(0, 3, 1, 2)
        channels = [2, 1, 0, 3][: bgrBCHW.shape[1]]
        return torch.from_numpy(bgrBCHW)[:, channels].to(torch.float32) / 255.0


def toCV(tensor):
    if tensor.ndim == 3:
        tensor = tensor[None, ...]

    bgrBCHW = tensor[:, [2, 1, 0, 3][: tensor.shape[1]]]
    bgrBHWC = bgrBCHW.permute(0, 2, 3, 1)

    return (bgrBHWC.to(torch.float32) * 255).round().to(torch.uint8).cpu().numpy()


def fromPngBytes(bytes):
    intensor = torch.tensor(np.frombuffer(bytes, dtype=np.uint8))
    asuint8 = torchvision.io.decode_image(
        intensor, torchvision.io.image.ImageReadMode.RGB_ALPHA
    )
    return asuint8[None, ...].to(torch.float32) / 255


# Images with alpha will be slow for now. TODO: Move to OpenCV (torchvision does not support encoding alpha images)
def toPngBytes(tensor):
    tensor = tensor.to("cpu")

    if tensor.ndim == 3:
        tensor = tensor[None, ...]

    if tensor.shape[1] == 1 or tensor.shape[1] == 3:
        tensor = (tensor.to(torch.float32) * 255).round().to(torch.uint8)
        pngs = [torchvision.io.encode_png(image) for image in tensor]
        return [png.numpy().tobytes() for png in pngs]
    elif tensor.shape[1] == 4:
        images = toCV(tensor)
        return [cv.imencode(".png", image)[1].tobytes() for image in images]
    else:
        print(f"Don't know how to save PNGs with {tensor.shape[1]} channels")
        return []


def normalise_tensor(tensor: torch.Tensor, channels: int = 3) -> torch.Tensor:
    # Process depth map
    if tensor.ndim == 3:
        tensor = tensor[None, ...]

    if channels == 1:
        return tensor[:, [0]]
    elif channels == 3:
        return tensor[:, [0, 1, 2]] if tensor.shape[1] >= 3 else tensor[:, [0, 0, 0]]
    elif channels == 4:
        if tensor.shape[1] >= 4:
            return tensor[:, [0, 1, 2, 3]]
        else:
            alpha = torch.zeros(tensor.shape[0], 1, *tensor.shape[2:])
            tensor = normalise_tensor(tensor, 3)
            return torch.concat((tensor, alpha), dim=1)

    raise ValueError(f"Unknown number of channels {channels}")


# TOOD: This won't work on images with alpha
def levels(tensor, in0, in1, out0, out1):
    c = (out1 - out0) / (in1 - in0)
    return ((tensor - in0) * c + out0).clamp(0, 1)


def invert(tensor):
    return 1 - tensor


# 0, 1, 2, 3 = r, g, b, a | 4 = 0 | 5 = 1 | 6 = drop
# TODO: These are from generation.proto, but we should be nicer about the mapping
def channelmap(tensor, srcchannels):
    # Any that are 6 won't be in final output
    outchannels = [x for x in srcchannels if x != 6]
    # Any channel request that is higher than channels available, just use channel 0
    # (This also deals with channels we will later fill with zero or one)
    cpychannels = [x if x < tensor.shape[1] else 0 for x in outchannels]

    # Copy the desired source channel into place (or the first channel if we will replace in the next step)
    tensor = tensor[:, cpychannels]

    # Replace any channels with 0 or 1 if requested
    for i, c in enumerate(outchannels):
        if c == 4:
            tensor[:, i] = torch.zeros_like(tensor[0][i])
        elif c == 5:
            tensor[:, i] = torch.ones_like(tensor[0][i])

    return tensor


def gaussianblur(tensor, sigma):
    if np.isscalar(sigma):
        sigma = (sigma, sigma)
    kernel = [ceil(sigma[0] * 6), ceil(sigma[1] * 6)]
    kernel = [kernel[0] - kernel[0] % 2 + 1, kernel[1] - kernel[1] % 2 + 1]
    return torchvision.transforms.functional.gaussian_blur(tensor, kernel, sigma)


def directionalblur(tensor, sigma, direction: Literal["up", "down"], repeat_count=256):
    orig = tensor
    sigma /= sqrt(repeat_count)

    for _ in range(repeat_count):
        tensor = gaussianblur(tensor, sigma)
        if direction == "down":
            tensor = torch.minimum(tensor, orig)
        else:
            tensor = torch.maximum(tensor, orig)

    return tensor


def crop(tensor, top, left, height, width):
    return tensor[:, :, top : top + height, left : left + width]


def rescale(
    tensor,
    height,
    width,
    fit: Literal["strict", "cover", "contain"],
    pad_mode="constant",
):
    # Get the original height and width
    orig_h, orig_w = tensor.shape[-2], tensor.shape[-1]
    # Calculate the scaling factors to hit the target height
    scale_h, scale_w = orig_h / height, orig_w / width

    if fit == "cover":
        scale_h = scale_w = max(scale_h, scale_w)
    elif fit == "contain":
        scale_h = scale_w = min(scale_h, scale_w)

    # Do the resize
    tensor = resize_right(
        tensor,
        [scale_h, scale_w],
        interp_method=interp_methods.lanczos2,
        antialiasing=False,
    ).clamp(0, 1)

    # Get the result height and width
    res_h, res_w = tensor.shape[-2], tensor.shape[-1]

    # Calculate the "half error", negative is too much, positive is too little
    err_h, err_w = (height - res_h) // 2, (width - res_w) // 2

    # Crop any extra off
    tensor = crop(
        tensor, -err_h if err_h < 0 else 0, -err_w if err_w < 0 else 0, height, width
    )

    # Pad any missing
    pad = [err_w, width - err_w] if err_w > 0 else [0, 0]
    pad += [err_h, height - err_h] if err_h > 0 else [0, 0]

    return torch.nn.functional.pad(tensor, pad, pad_mode)


def canny_edge(tensor, low_threshold, high_threshold):
    cvi = toCV(tensor)
    res = []

    for i in range(cvi.shape[0]):
        cvii = cvi[i]
        edges = cv.Canny(cvii, low_threshold, high_threshold)
        res.append(fromCV(edges))

    return torch.cat(res, dim=0)
