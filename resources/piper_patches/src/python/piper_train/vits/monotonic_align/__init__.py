import torch
import numpy as np
from .core import maximum_path_c

def maximum_path(neg_cent, mask):
    device = neg_cent.device
    dtype = neg_cent.dtype
    neg_cent = neg_cent.data.cpu().numpy().astype(np.float32)
    path = np.zeros(neg_cent.shape, dtype=np.int32)

    t_t = mask.sum(1).data.cpu().numpy().astype(np.int32)
    t_s = mask.sum(2).data.cpu().numpy().astype(np.int32)
    maximum_path_c(path, neg_cent, t_t, t_s)
    return torch.from_numpy(path).to(device=device, dtype=dtype)