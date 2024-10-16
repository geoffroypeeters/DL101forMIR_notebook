#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
File: model_module.py
Description: factory to create pytorch DL-models from *.yaml configuration file
Author: geoffroy.peeters@telecom-paris.fr
"""


import torch
import torch.nn as nn
import numpy as np
from model_module import SincConv_fast
from torch.nn.utils import weight_norm


# ConvNeXt PAPER: https://arxiv.org/pdf/2201.03545
# ConvNeXt CODE: https://github.com/facebookresearch/ConvNeXt/blob/main/models/convnext.py
class ConvNeXtBlock(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size=7, drop_path=0.0):
        super(ConvNeXtBlock, self).__init__()
        
        # 1. Depthwise convolution (spatial convolution with large kernel)
        self.dwconv = nn.Conv2d(in_channels, in_channels, kernel_size=kernel_size, padding=kernel_size // 2, groups=in_channels)
        
        # 2. Layer normalization applied across channels
        self.norm = nn.LayerNorm(in_channels, eps=1e-6)  # LayerNorm is applied after permuting to (B, C, H, W)
        
        # 3. Pointwise convolution to project to higher dimensions (expanding and compressing channels)
        self.pwconv1 = nn.Linear(in_channels, 4 * in_channels)  # expand channels by 4x
        self.act = nn.GELU()  # GELU activation
        self.pwconv2 = nn.Linear(4 * in_channels, out_channels)  # project back to original channels
        
        # 4. Stochastic depth (optional) for better regularization
        self.drop_path = nn.Identity() if drop_path == 0 else StochasticDepth(drop_path)
    
    def forward(self, x):
        # Input: (B, C, H, W)
        residual = x

        # 1. Depthwise convolution
        x = self.dwconv(x)
        
        # 2. LayerNorm after permute to (B, H, W, C)
        x = x.permute(0, 2, 3, 1)  # (B, C, H, W) -> (B, H, W, C)
        x = self.norm(x)
        
        # 3. Pointwise convolutions + GELU
        x = self.pwconv1(x)
        x = self.act(x)
        x = self.pwconv2(x)
        
        # 4. Drop path (if applicable) and residual connection
        x = x.permute(0, 3, 1, 2)  # (B, H, W, C) -> (B, C, H, W)
        x = self.drop_path(x) + residual  # Add residual connection
        
        return x

class StochasticDepth(nn.Module):
    """Drop paths (stochastic depth) per sample (when applied in the main path of residual blocks)."""
    def __init__(self, drop_prob=None):
        super(StochasticDepth, self).__init__()
        self.drop_prob = drop_prob
    
    def forward(self, x):
        if not self.training or self.drop_prob == 0.0:
            return x
        keep_prob = 1 - self.drop_prob
        # Sample binary mask
        shape = (x.shape[0],) + (1,) * (x.ndim - 1)
        random_tensor = keep_prob + torch.rand(shape, dtype=x.dtype, device=x.device)
        binary_mask = torch.floor(random_tensor)
        return x / keep_prob * binary_mask




# TCN paper: https://arxiv.org/pdf/1803.01271
# TCN code: https://github.com/locuslab/TCN
class Chomp1d(nn.Module):
    def __init__(self, chomp_size):
        super(Chomp1d, self).__init__()
        self.chomp_size = chomp_size
    def forward(self, x):
        return x[:, :, :-self.chomp_size].contiguous()


class TemporalBlock(nn.Module):
    def __init__(self, n_inputs, n_outputs, kernel_size, stride, dilation, padding, dropout=0.2):
        super(TemporalBlock, self).__init__()
        self.conv1 = weight_norm(nn.Conv1d(n_inputs, n_outputs, kernel_size,
                                           stride=stride, padding=padding, dilation=dilation))
        self.chomp1 = Chomp1d(padding)
        self.relu1 = nn.ReLU()
        self.dropout1 = nn.Dropout(dropout)

        self.conv2 = weight_norm(nn.Conv1d(n_outputs, n_outputs, kernel_size,
                                           stride=stride, padding=padding, dilation=dilation))
        self.chomp2 = Chomp1d(padding)
        self.relu2 = nn.ReLU()
        self.dropout2 = nn.Dropout(dropout)

        self.net = nn.Sequential(self.conv1, self.chomp1, self.relu1, self.dropout1,
                                 self.conv2, self.chomp2, self.relu2, self.dropout2)
        self.downsample = nn.Conv1d(n_inputs, n_outputs, 1) if n_inputs != n_outputs else None
        self.relu = nn.ReLU()
        self.init_weights()

    def init_weights(self):
        self.conv1.weight.data.normal_(0, 0.01)
        self.conv2.weight.data.normal_(0, 0.01)
        if self.downsample is not None:
            self.downsample.weight.data.normal_(0, 0.01)

    def forward(self, x):
        out = self.net(x)
        res = x if self.downsample is None else self.downsample(x)
        return self.relu(out + res)


class TemporalConvNet(nn.Module):
    def __init__(self, num_inputs, num_channels, kernel_size=2, dropout=0.2):
        super(TemporalConvNet, self).__init__()
        layers = []
        num_levels = len(num_channels)
        for i in range(num_levels):
            dilation_size = 2 ** i
            in_channels = num_inputs if i == 0 else num_channels[i-1]
            out_channels = num_channels[i]
            layers += [TemporalBlock(in_channels, out_channels, kernel_size, stride=1, dilation=dilation_size,
                                     padding=(kernel_size-1) * dilation_size, dropout=dropout)]

        self.network = nn.Sequential(*layers)

    def forward(self, x):
        return self.network(x)




# Paper: 
# Code: 
class ResidualBlock(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size = 3, stride = 1):
        super(ResidualBlock, self).__init__()
        self.conv1 = nn.Sequential(
                        nn.Conv2d(in_channels, out_channels, kernel_size = kernel_size, stride = stride, padding = 'same'),
                        nn.BatchNorm2d(out_channels),
                        nn.ReLU())
        self.conv2 = nn.Sequential(
                        nn.Conv2d(out_channels, out_channels, kernel_size = kernel_size, stride = 1, padding = 'same'),
                        nn.BatchNorm2d(out_channels))
        self.downsample = False
        
        if in_channels != out_channels:
            self.downsample = True
            self.conv_ds = nn.Conv2d(in_channels, out_channels, kernel_size = 1, stride = 1, padding = 'same')
        self.relu = nn.ReLU()
        self.out_channels = out_channels

    def forward(self, x):
        residual = x
        out = self.conv1(x)
        out = self.conv2(out)
        if self.downsample: residual = self.conv_ds(x)
        out += residual
        out = self.relu(out)
        return out



# Paper: 
# Code: https://github.com/seungjunlee96/Depthwise-Separable-Convolution_Pytorch/blob/master/DepthwiseSeparableConvolution/DepthwiseSeparableConvolution.py
class depthwise_separable_conv(nn.Module):
    def __init__(self, nin, kernels_per_layer, nout, kernel_size=3, padding=1):
        super(depthwise_separable_conv, self).__init__()
        self.depthwise = nn.Conv2d(nin, nin * kernels_per_layer, kernel_size=kernel_size, padding=padding, groups=nin)
        self.pointwise = nn.Conv2d(nin * kernels_per_layer, nout, kernel_size=1)
    def forward(self, x):
        out = self.depthwise(x)
        out = self.pointwise(out)
        return out





# Code: https://github.com/furkanyesiler/move
def f_autopool_weights(data, autopool_param):
    """
    Calculating the autopool weights for a given tensor
    :param data: tensor for calculating the softmax weights with autopool
    :return: softmax weights with autopool

    see https://arxiv.org/pdf/1804.10070
    alpha=0: unweighted mean
    alpha=1: softmax
    alpha=inf: max-pooling
    """
    # --- x: (batch, 256, 1, T)
    x = data * autopool_param
    # --- max_values: (batch, 256, 1, 1)
    max_values = torch.max(x, dim=3, keepdim=True).values
    # --- softmax (batch, 256, 1, T)
    softmax = torch.exp(x - max_values)
    # --- weights (batch, 256, 1, T)
    weights = softmax / torch.sum(softmax, dim=3, keepdim=True)
    return weights




def f_get_next_size(in_L, k, s):
    """ gives resulting output size of a convolution on a vector of len in_L with a kernel of size k and stride s """
    return int(np.floor( (in_L-k)/s+1 ))


def f_get_activation(activation_type):
    """ return the corresponding activation class """
    if activation_type=='Sigmoid': 
        activation = nn.Sigmoid()
    elif activation_type=='Softmax': 
        activation = nn.Softmax()
    elif activation_type=='ReLU': 
        activation = nn.ReLU()
    elif activation_type=='LeakyReLU': 
        activation = nn.LeakyReLU()
    elif activation_type=='PReLU':
        activation = nn.PReLU()
    return activation


class nnAbs(nn.Module):
    """ encapsultate *.abs as an object """
    def __init__(self):
        super().__init__()
    def forward(self, X):
        return torch.abs(X)

class nnMean(nn.Module):
    """ encapsultate *.mean as an object """
    def __init__(self, dim, keepdim):
        super().__init__()
        self.dim = dim
        self.keepdim = keepdim
    def forward(self, X):
        return torch.mean(X, self.dim, self.keepdim)

class nnMax(nn.Module):
    """ encapsultate *.mean  as an object """
    def __init__(self, dim, keepdim):
        super().__init__()
        self.dim = dim
        self.keepdim = keepdim
    def forward(self, X):
        out, _ = torch.max(X, self.dim, self.keepdim)
        return out

class nnSqueeze(nn.Module):
    """ encapsultate .Squeeze as an object """
    def __init__(self, dim):
        super().__init__()
        self.dim = dim
    def forward(self, X):
        out = X.squeeze(dim=self.dim)
        return out

class nnPermute(nn.Module):
    """ encapsultate *.permute as an object """
    def __init__(self, shape):
        super().__init__()
        self.shape = shape
    def forward(self, X):
        return X.permute(self.shape)

class nnBatchNorm1dT(nn.Module):
    """ perform BatchNorm1d over transposed vector (in case input format is (B,T,C) """
    def __init__(self, num_features):
        super().__init__()
        self.module = nn.BatchNorm1d(num_features)
    def forward(self, X):
        return self.module(X.permute(0,2,1)).permute(0,2,1)

class nnEmpty(nn.Module):
    """ encapsultate do-nothing as an object """
    def __init__(self):
        super().__init__()
    def forward(self, X):
        return X

class nnSoftmaxWeight(nn.Module):
    """ 
    Perform attention weighing based on softmax with channel splitting
    Code from https://github.com/furkanyesiler/move 
    """
    def __init__(self, nb_channel):
        super().__init__()
        self.nb_channel = nb_channel
    def forward(self, X):
        weights = torch.nn.functional.softmax(X[:, int(self.nb_channel/2):], dim=3)
        X = torch.sum(X[:, :int(self.nb_channel/2)] * weights, dim=3, keepdim=True)
        return X

class nnAutoPoolWeight(nn.Module):
    """ 
    Perform attention weighing based on auto-pool (instead of softmax)
    Code from https://github.com/furkanyesiler/move 
    """
    def __init__(self):
        super().__init__()
        self.autopool_param = nn.Parameter(torch.tensor(0.).float())
    def forward(self, X):
        weights = f_autopool_weights(X, self.autopool_param)
        X = torch.sum(X * weights, dim=3, keepdim=True)
        return X

class nnAutoPoolWeightSplit(nn.Module):
    """ 
    Perform attention weighing based on auto-pool (instead of softmax) with channel splitting
    Code from https://github.com/furkanyesiler/move 
    """
    def __init__(self, nb_channel):
        super().__init__()
        self.autopool_param = nn.Parameter(torch.tensor(0.).float())
        self.nb_channel = nb_channel
    def forward(self, X):
        weights = f_autopool_weights(X[:, int(self.nb_channel/2):], self.autopool_param)
        X = torch.sum(X[:, :int(self.nb_channel/2):] * weights, dim=3, keepdim=True)
        return X



def f_parse_component(module_type, param, current_input_dim):
    """ 
    Parse module_type and param parameters to create a new NN layers, return the new dimensions of the tensor

    Parameters
    ----------
    module_type: str
        type of the module to be added (such as 'LayerNorm', 'BatchNorm1d', ...)
    param: dictionary
        the parameters for the given module
    current_input_dim: array of int
        dimension of the inputs before adding this specific layer

    Returns
    -------
    module
        the newly created nn.Module
    current_input_dim: : array of int
        dimension of the outputs aftere adding this specific layer
   """

    # --- FC:       B, D
    # --- Conv1D:   B, C, T
    # --- Conv2D:   B, C, H=Freq, W=Time

    #print(module_type, param)
    if module_type=='LayerNorm':
        if param.normalized_shape==-1:
            param.normalized_shape = current_input_dim[1:] # --- B, C, T
        module = nn.LayerNorm(normalized_shape=param.normalized_shape)

    elif module_type=='BatchNorm1d':
        if param.num_features==-1:
            param.num_features = current_input_dim[-1] # --- (B, D) or (B, T, D)
        if 'affine' not in param.keys():
            param.affine = True
        module = nn.BatchNorm1d(num_features=param.num_features, affine=param.affine)

    elif module_type=='BatchNorm1dT':
        if param.num_features==-1:
            param.num_features = current_input_dim[-1] # --- (B, D) or (B, T, D)
        module = nnBatchNorm1dT(num_features=param.num_features)

    elif module_type=='BatchNorm2d':
        if param.num_features==-1:
            param.num_features = current_input_dim[1] # --- (B, C, H, W)
        module = nn.BatchNorm2d(num_features=param.num_features)

    elif module_type=='SincNet': # --- B, C, T
        if param.in_channels==-1:
            param.in_channels=current_input_dim[1]
        module = SincConv_fast(in_channels=param.in_channels, out_channels=param.out_channels, kernel_size=param.kernel_size, stride=param.stride, sample_rate=param.sr_hz)
        current_input_dim[1] = param.out_channels
        current_input_dim[2] = f_get_next_size(current_input_dim[2], param.kernel_size, param.stride)

    elif module_type=='Conv1d': # --- B, C, T
        if param.in_channels==-1:
            param.in_channels=current_input_dim[1]
        module = nn.Conv1d(in_channels=param.in_channels, out_channels=param.out_channels, kernel_size=param.kernel_size, stride=param.stride)
        current_input_dim[1] = param.out_channels
        current_input_dim[2] = f_get_next_size(current_input_dim[2], param.kernel_size, param.stride)

    elif module_type=='Conv1dTCN': # --- B, C, T
        if param.in_channels==-1:
            param.in_channels=current_input_dim[1]
        module = TemporalConvNet(num_inputs=param.in_channels, num_channels=param.num_channels)
        current_input_dim[1] = param.num_channels[-1]
        
    elif module_type=='Conv2d': # --- B, C, H=Freq, W=Time
        if param.in_channels==-1:
            param.in_channels=current_input_dim[1]
        if 'padding' not in param.keys():
            param.padding = 'valid'
        module = nn.Conv2d(in_channels=param.in_channels, out_channels=param.out_channels, kernel_size=param.kernel_size, stride=param.stride, padding=param.padding)
        current_input_dim[1] = param.out_channels
        if param.padding != 'same':
            current_input_dim[2] = f_get_next_size(current_input_dim[2], param.kernel_size[0], param.stride[0])
            current_input_dim[3] = f_get_next_size(current_input_dim[3], param.kernel_size[1], param.stride[1])

    elif module_type=='Conv2dDS': # --- B, C, H=Freq, W=Time
        if param.in_channels==-1:
            param.in_channels=current_input_dim[1]
        if 'padding' not in param.keys():
            param.padding = 'valid'
        module = depthwise_separable_conv(nin=param.in_channels, kernels_per_layer=1, nout=param.out_channels, kernel_size=param.kernel_size, padding=param.padding)
        current_input_dim[1] = param.out_channels
        if param.padding != 'same':
            current_input_dim[2] = f_get_next_size(current_input_dim[2], param.kernel_size[0], param.stride[0])
            current_input_dim[3] = f_get_next_size(current_input_dim[3], param.kernel_size[1], param.stride[1])

    elif module_type=='Conv2dRes': # --- B, C, H=Freq, W=Time
        if param.in_channels==-1:
            param.in_channels=current_input_dim[1]
        if param.padding != 'same':
            print(f'only work for padding=same, got {param}')
        if param.stride != 1:
            print(f'only work for stride=1, got {param}')
        module = ResidualBlock(in_channels=param.in_channels, out_channels=param.out_channels, kernel_size = param.kernel_size, stride = 1)
        current_input_dim[1] = param.out_channels
    
    elif module_type=='Conv2dNext': # --- B, C, H=Freq, W=Time
        if param.in_channels==-1:
            param.in_channels = current_input_dim[1]
        if param.out_channels != param.in_channels:
            print('param.out_channels should = param.in_channels because of residual connection')
        if param.padding != 'same':
            print(f'only work for padding=same, got {param}')
        if param.stride != 1:
            print(f'only work for stride=1, got {param}')
        module = ConvNeXtBlock(in_channels=param.in_channels, out_channels=param.out_channels, kernel_size=7, drop_path=0.0)
        current_input_dim[1] = param.out_channels

    elif module_type=='ConvTranspose2d':
        if param.in_channels==-1:
            param.in_channels = current_input_dim[1] 
        module = nn.ConvTranspose2d(in_channels=param.in_channels, out_channels=param.out_channels, kernel_size=param.kernel_size, stride=param.stride)
        current_input_dim[1] = param.out_channels
        current_input_dim[2] *= param.stride[0]
        current_input_dim[3] *= param.stride[1]

    elif module_type=='MaxPool1d':  # --- B, C, T
        if 'stride' not in param.keys():
            param.stride = param.kernel_size
        module = nn.MaxPool1d(kernel_size=param.kernel_size, stride=param.stride)
        current_input_dim[2] = int(np.floor(current_input_dim[2]/param.kernel_size)) 

    elif module_type=='MaxPool2d': # --- B, C, H=Freq, W=Time
        if 'stride' not in param.keys():
            param.stride = param.kernel_size
        module = nn.MaxPool2d(kernel_size=param.kernel_size, stride=param.stride)
        current_input_dim[2] = int(np.floor(current_input_dim[2]/param.kernel_size[0]))
        current_input_dim[3] = int(np.floor(current_input_dim[3]/param.kernel_size[1]))

    elif module_type=='Linear': 
        if param.in_features==-1:
            param.in_features = current_input_dim[-1] # --- (B, D) or (B, T, D)
        module = nn.Linear(in_features=param.in_features , out_features=param.out_features)
        current_input_dim[-1] = param.out_features

    elif module_type=='Activation':   
        module = f_get_activation(param)

    elif module_type=='Dropout':   
        module = nn.Dropout(param.p)

    elif module_type=='Flatten':
        module = nn.Flatten(param.start_dim)
        current_input_dim =  [current_input_dim[0], current_input_dim[1]*current_input_dim[2]]

    elif module_type=='Squeeze':
        module = nnSqueeze(param.dim)
        current_input_dim = [c for c in current_input_dim if c > 1]

    elif module_type=='Permute':
        module = nnPermute(param.shape)
        current_input_dim =  [current_input_dim[s] for s in param.shape]

    elif module_type=='Mean':
        if 'keepdim' not in param.keys(): param.keepdim = False
        module = nnMean(param.dim, param.keepdim)
        if param.keepdim is False:
            current_input_dim =  [current_input_dim[0], current_input_dim[1]]

    elif module_type=='Max':
        if 'keepdim' not in param.keys(): param.keepdim = False
        module = nnMax(param.dim, param.keepdim)
        if param.keepdim is False:
            current_input_dim =  [current_input_dim[0], current_input_dim[1]]

    elif module_type=='AutoPoolWeight':
        module = nnAutoPoolWeight()
        current_input_dim[3] = 1

    elif module_type=='AutoPoolWeightSplit':
        module = nnAutoPoolWeightSplit(current_input_dim[1])
        current_input_dim[1] = int(current_input_dim[1]/2)
        current_input_dim[3] = 1

    elif module_type=='SoftmaxWeight':
        module = nnSoftmaxWeight(current_input_dim[1])
        current_input_dim[1] = int(current_input_dim[1]/2)
        current_input_dim[3] = 1

    elif module_type=='AbsLayer':
        module = nnAbs()

    elif module_type=='DoubleChannel': # --- for U-Net
        module = nnEmpty()
        current_input_dim[1] *= 2

    else:
        print(f'UNKNOWN module "{module_type}"')

    return module, current_input_dim
