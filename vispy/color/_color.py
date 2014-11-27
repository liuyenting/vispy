# -*- coding: utf-8 -*-
# Copyright (c) 2014, Vispy Development Team.
# Distributed under the (new) BSD License. See LICENSE.txt for more info.

from __future__ import division  # just to be safe...

import numpy as np
from copy import deepcopy

from ..ext.six import string_types
from ..util import logger
from ._color_dict import _color_dict


###############################################################################
# User-friendliness helpers

def _string_to_rgb(color):
    """Convert user string or hex color to color array (length 3 or 4)"""
    if not color.startswith('#'):
        if color.lower() not in _color_dict:
            raise ValueError('Color "%s" unknown' % color)
        color = _color_dict[color]
        assert color[0] == '#'
    # hex color
    color = color[1:]
    lc = len(color)
    if lc in (3, 4):
        color = ''.join(c + c for c in color)
        lc = len(color)
    if lc not in (6, 8):
        raise ValueError('Hex color must have exactly six or eight '
                         'elements following the # sign')
    color = np.array([int(color[i:i+2], 16) / 255. for i in range(0, lc, 2)])
    return color


def _user_to_rgba(color, expand=True, clip=False):
    """Convert color(s) from any set of fmts (str/hex/arr) to RGB(A) array"""
    if color is None:
        color = np.zeros(4, np.float32)
    if isinstance(color, string_types):
        color = _string_to_rgb(color)
    elif isinstance(color, ColorArray):
        color = color.rgba
    # We have to treat this specially
    elif isinstance(color, (list, tuple)):
        if any(isinstance(c, string_types) for c in color):
            color = [_user_to_rgba(c, expand=expand, clip=clip) for c in color]
            if any(len(c) > 1 for c in color):
                raise RuntimeError('could not parse colors, are they nested?')
            color = [c[0] for c in color]
    color = np.atleast_2d(color).astype(np.float32)
    if color.shape[1] not in (3, 4):
        raise ValueError('color must have three or four elements')
    if expand and color.shape[1] == 3:  # only expand if requested
        color = np.concatenate((color, np.ones((color.shape[0], 1))),
                               axis=1)
    if color.min() < 0 or color.max() > 1:
        if clip:
            color = np.clip(color, 0, 1)
        else:
            raise ValueError("Color values must be between 0 and 1 (or use "
                             "clip=True to automatically clip the values).")
    return color


def _check_color_dim(val):
    """Ensure val is Nx(n_col), usually Nx3"""
    val = np.atleast_2d(val)
    if val.shape[1] not in (3, 4):
        raise RuntimeError('Value must have second dimension of size 3 or 4')
    return val, val.shape[1]


def _array_clip_val(val):
    """Helper to turn val into array and clip between 0 and 1"""
    val = np.array(val)
    if val.max() > 1 or val.min() < 0:
        logger.warning('value will be clipped between 0 and 1')
    val[...] = np.clip(val, 0, 1)
    return val


###############################################################################
# RGB<->HEX conversion

def _hex_to_rgba(hexs):
    """Convert hex to rgba, permitting alpha values in hex"""
    hexs = np.atleast_1d(np.array(hexs, '|U9'))
    out = np.ones((len(hexs), 4), np.float32)
    for hi, h in enumerate(hexs):
        assert isinstance(h, string_types)
        off = 1 if h[0] == '#' else 0
        assert len(h) in (6+off, 8+off)
        e = (len(h)-off) // 2
        out[hi, :e] = [int(h[i:i+2], 16) / 255.
                       for i in range(off, len(h), 2)]
    return out


def _rgb_to_hex(rgbs):
    """Convert rgb to hex triplet"""
    rgbs, n_dim = _check_color_dim(rgbs)
    return np.array(['#%02x%02x%02x' % tuple((255*rgb[:3]).astype(np.uint8))
                     for rgb in rgbs], '|U7')


###############################################################################
# RGB<->HSV conversion

def _rgb_to_hsv(rgbs):
    """Convert Nx3 or Nx4 rgb to hsv"""
    rgbs, n_dim = _check_color_dim(rgbs)
    hsvs = list()
    for rgb in rgbs:
        rgb = rgb[:3]  # don't use alpha here
        idx = np.argmax(rgb)
        val = rgb[idx]
        c = val - np.min(rgb)
        if c == 0:
            hue = 0
            sat = 0
        else:
            if idx == 0:  # R == max
                hue = ((rgb[1] - rgb[2]) / c) % 6
            elif idx == 1:  # G == max
                hue = (rgb[2] - rgb[0]) / c + 2
            else:  # B == max
                hue = (rgb[0] - rgb[1]) / c + 4
            hue *= 60
            sat = c / val
        hsv = [hue, sat, val]
        hsvs.append(hsv)
    hsvs = np.array(hsvs, dtype=np.float32)
    if n_dim == 4:
        hsvs = np.concatenate((hsvs, rgbs[:, 3]), axis=1)
    return hsvs


def _hsv_to_rgb(hsvs):
    """Convert Nx3 or Nx4 hsv to rgb"""
    hsvs, n_dim = _check_color_dim(hsvs)
    # In principle, we *might* be able to vectorize this, but might as well
    # wait until a compelling use case appears
    rgbs = list()
    for hsv in hsvs:
        c = hsv[1] * hsv[2]
        m = hsv[2] - c
        hp = hsv[0] / 60
        x = c * (1 - abs(hp % 2 - 1))
        if 0 <= hp < 1:
            r, g, b = c, x, 0
        elif hp < 2:
            r, g, b = x, c, 0
        elif hp < 3:
            r, g, b = 0, c, x
        elif hp < 4:
            r, g, b = 0, x, c
        elif hp < 5:
            r, g, b = x, 0, c
        else:
            r, g, b = c, 0, x
        rgb = [r + m, g + m, b + m]
        rgbs.append(rgb)
    rgbs = np.array(rgbs, dtype=np.float32)
    if n_dim == 4:
        rgbs = np.concatenate((rgbs, hsvs[:, 3]), axis=1)
    return rgbs


###############################################################################
# RGB<->CIELab conversion

# These numbers are adapted from MIT-licensed MATLAB code for
# Lab<->RGB conversion. They provide an XYZ<->RGB conversion matrices,
# w/D65 white point normalization built in.

#_rgb2xyz = np.array([[0.412453, 0.357580, 0.180423],
#                     [0.212671, 0.715160, 0.072169],
#                     [0.019334, 0.119193, 0.950227]])
#_white_norm = np.array([0.950456, 1.0, 1.088754])
#_rgb2xyz /= _white_norm[:, np.newaxis]
#_rgb2xyz_norm = _rgb2xyz.T
_rgb2xyz_norm = np.array([[0.43395276, 0.212671, 0.01775791],
                         [0.37621941, 0.71516, 0.10947652],
                         [0.18982783, 0.072169, 0.87276557]])

#_xyz2rgb = np.array([[3.240479, -1.537150, -0.498535],
#                     [-0.969256, 1.875992, 0.041556],
#                     [0.055648, -0.204043, 1.057311]])
#_white_norm = np.array([0.950456, 1., 1.088754])
#_xyz2rgb *= _white_norm[np.newaxis, :]
_xyz2rgb_norm = np.array([[3.07993271, -1.53715, -0.54278198],
                          [-0.92123518, 1.875992, 0.04524426],
                          [0.05289098, -0.204043, 1.15115158]])


def _rgb_to_lab(rgbs):
    rgbs, n_dim = _check_color_dim(rgbs)
    # convert RGB->XYZ
    xyz = rgbs[:, :3].copy()  # a misnomer for now but will end up being XYZ
    over = xyz > 0.04045
    xyz[over] = ((xyz[over] + 0.055) / 1.055) ** 2.4
    xyz[~over] /= 12.92
    xyz = np.dot(xyz, _rgb2xyz_norm)
    over = xyz > 0.008856
    xyz[over] = xyz[over] ** (1. / 3.)
    xyz[~over] = 7.787 * xyz[~over] + 0.13793103448275862

    # Convert XYZ->LAB
    L = (116. * xyz[:, 1]) - 16
    a = 500 * (xyz[:, 0] - xyz[:, 1])
    b = 200 * (xyz[:, 1] - xyz[:, 2])
    labs = [L, a, b]
    # Append alpha if necessary
    if n_dim == 4:
        labs.append(np.atleast1d(rgbs[:, 3]))
    labs = np.array(labs, order='F').T  # Becomes 'C' order b/c of .T
    return labs


def _lab_to_rgb(labs):
    """Convert Nx3 or Nx4 lab to rgb"""
    # adapted from BSD-licensed work in MATLAB by Mark Ruzon
    # Based on ITU-R Recommendation BT.709 using the D65
    labs, n_dim = _check_color_dim(labs)

    # Convert Lab->XYZ (silly indexing used to preserve dimensionality)
    y = (labs[:, 0] + 16.) / 116.
    x = (labs[:, 1] / 500.) + y
    z = y - (labs[:, 2] / 200.)
    xyz = np.concatenate(([x], [y], [z]))  # 3xN
    over = xyz > 0.2068966
    xyz[over] = xyz[over] ** 3.
    xyz[~over] = (xyz[~over] - 0.13793103448275862) / 7.787

    # Convert XYZ->LAB
    rgbs = np.dot(_xyz2rgb_norm, xyz).T
    over = rgbs > 0.0031308
    rgbs[over] = 1.055 * (rgbs[over] ** (1. / 2.4)) - 0.055
    rgbs[~over] *= 12.92
    if n_dim == 4:
        rgbs = np.concatenate((rgbs, labs[:, 3]), axis=1)
    rgbs = np.clip(rgbs, 0., 1.)
    return rgbs


###############################################################################
# Color Array

class ColorArray(object):
    """An array of colors

    Parameters
    ----------
    color : str | tuple | list of colors
        If str, can be any of the names in ``vispy.color.get_color_names``.
        Can also be a hex value if it starts with ``'#'`` as ``'#ff0000'``.
        If array-like, it must be an Nx3 or Nx4 array-like object.
        Can also be a list of colors, such as
        ``['red', '#00ff00', ColorArray('blue')]``.
    alpha : float | None
        If no alpha is not supplied in ``color`` entry and ``alpha`` is None,
        then this will default to 1.0 (opaque). If float, it will override
        any alpha values in ``color``, if provided.

    Examples
    --------
    There are many ways to define colors. Here are some basic cases:

        >>> from vispy.color import ColorArray
        >>> r = ColorArray('red')  # using string name
        >>> r
        <ColorArray: 1 color ((1.0, 0.0, 0.0, 1.0))>
        >>> g = ColorArray((0, 1, 0, 1))  # RGBA tuple
        >>> b = ColorArray('#0000ff')  # hex color
        >>> w = ColorArray()  # defaults to black
        >>> w.rgb = r.rgb + g.rgb + b.rgb
        >>> w == ColorArray('white')
        True
        >>> w.alpha = 0
        >>> w
        <ColorArray: 1 color ((1.0, 1.0, 1.0, 0.0))>
        >>> rgb = ColorArray(['r', (0, 1, 0), '#0000FFFF'])
        >>> rgb
        <ColorArray: 3 colors ((1.0, 0.0, 0.0, 1.0) ... (1.0, 0.0, 0.0, 1.0))>
        >>> rgb == ColorArray(['red', '#00ff00', ColorArray('blue')])
        True

    Notes
    -----
    Under the hood, this class stores data in RGBA format suitable for use
    on the GPU.
    """
    def __init__(self, color='black', alpha=None, clip=False):
        """Parse input type, and set attribute"""
        rgba = _user_to_rgba(color, clip=clip)
        if alpha is not None:
            rgba[:, 3] = alpha
        self._rgba = None
        self.rgba = rgba

    ###########################################################################
    # Builtins and utilities
    def copy(self):
        """Return a copy"""
        return deepcopy(self)

    @classmethod
    def _name(cls):
        """Helper to get the class name once it's been created"""
        return cls.__name__

    def __len__(self):
        return self._rgba.shape[0]

    def __repr__(self):
        nice_str = str(tuple(self._rgba[0]))
        plural = ''
        if len(self) > 1:
            plural = 's'
            nice_str += ' ... ' + str(tuple(self.rgba[-1]))
        # use self._name() here instead of hard-coding name in case
        # we eventually subclass this class
        return ('<%s: %i color%s (%s)>' % (self._name(), len(self),
                                           plural, nice_str))

    def __eq__(self, other):
        return np.array_equal(self._rgba, other._rgba)

    ###########################################################################
    def __getitem__(self, item):
        if isinstance(item, tuple):
            raise ValueError('ColorArray indexing is only allowed along '
                             'the first dimension.')
        subrgba = self._rgba[item]
        if subrgba.ndim == 1:
            assert len(subrgba) == 4
        elif subrgba.ndim == 2:
            assert subrgba.shape[1] in (3, 4)
        return ColorArray(subrgba)

    def __setitem__(self, item, value):
        if isinstance(item, tuple):
            raise ValueError('ColorArray indexing is only allowed along '
                             'the first dimension.')
        # value should be a RGBA array, or a ColorArray instance
        if isinstance(value, ColorArray):
            value = value.rgba
        self._rgba[item] = value

    # RGB(A)
    @property
    def rgba(self):
        """Nx4 array of RGBA floats"""
        return self._rgba.copy()

    @rgba.setter
    def rgba(self, val):
        """Set the color using an Nx4 array of RGBA floats"""
        # Note: all other attribute sets get routed here!
        # This method is meant to do the heavy lifting of setting data
        rgba = _user_to_rgba(val, expand=False)
        if self._rgba is None:
            self._rgba = rgba  # only on init
        else:
            self._rgba[:, :rgba.shape[1]] = rgba

    @property
    def rgb(self):
        """Nx3 array of RGB floats"""
        return self._rgba[:, :3].copy()

    @rgb.setter
    def rgb(self, val):
        """Set the color using an Nx3 array of RGB floats"""
        self.rgba = val

    @property
    def RGBA(self):
        """Nx4 array of RGBA uint8s"""
        return (self._rgba * 255).astype(np.uint8)

    @RGBA.setter
    def RGBA(self, val):
        """Set the color using an Nx4 array of RGBA uint8 values"""
        # need to convert to normalized float
        val = np.atleast_1d(val).astype(np.float32) / 255
        self.rgba = val

    @property
    def RGB(self):
        """Nx3 array of RGBA uint8s"""
        return np.round(self._rgba[:, :3] * 255).astype(int)

    @RGB.setter
    def RGB(self, val):
        """Set the color using an Nx3 array of RGB uint8 values"""
        # need to convert to normalized float
        val = np.atleast_1d(val).astype(np.float32) / 255.
        self.rgba = val

    @property
    def alpha(self):
        """Length-N array of alpha floats"""
        return self._rgba[:, 3]

    @alpha.setter
    def alpha(self, val):
        """Set the color using alpha"""
        self._rgba[:, 3] = _array_clip_val(val)

    ###########################################################################
    # HEX
    @property
    def hex(self):
        """Numpy array with N elements, each one a hex triplet string"""
        return _rgb_to_hex(self._rgba)

    @hex.setter
    def hex(self, val):
        """Set the color values using a list of hex strings"""
        self.rgba = _hex_to_rgba(val)

    ###########################################################################
    # HSV
    @property
    def hsv(self):
        """Nx3 array of HSV floats"""
        return self._hsv

    @hsv.setter
    def hsv(self, val):
        """Set the color values using an Nx3 array of HSV floats"""
        self.rgba = _hsv_to_rgb(val)

    @property
    def _hsv(self):
        """Nx3 array of HSV floats"""
        # this is done privately so that overriding functions work
        return _rgb_to_hsv(self._rgba[:, :3])

    @property
    def value(self):
        """Length-N array of color HSV values"""
        return self._hsv[:, 2]

    @value.setter
    def value(self, val):
        """Set the color using length-N array of (from HSV)"""
        hsv = self._hsv
        hsv[:, 2] = _array_clip_val(val)
        self.rgba = _hsv_to_rgb(hsv)

    def lighter(self, dv=0.1, copy=True):
        """Produce a lighter color (if possible)

        Parameters
        ----------
        dv : float
            Amount to increase the color value by.
        copy : bool
            If False, operation will be carried out in-place.

        Returns
        -------
        color : instance of ColorArray
            The lightened Color.
        """
        color = self.copy() if copy else self
        color.value += dv
        return color

    def darker(self, dv=0.1, copy=True):
        """Produce a darker color (if possible)

        Parameters
        ----------
        dv : float
            Amount to decrease the color value by.
        copy : bool
            If False, operation will be carried out in-place.

        Returns
        -------
        color : instance of ColorArray
            The darkened Color.
        """
        color = self.copy() if copy else self
        color.value -= dv
        return color

    ###########################################################################
    # Lab
    @property
    def lab(self):
        return _rgb_to_lab(self._rgba[:, :3])

    @lab.setter
    def lab(self, val):
        self.rgba = _lab_to_rgb(val)


class Color(ColorArray):
    """A single color

    Parameters
    ----------
    color : str | tuple
        If str, can be any of the names in ``vispy.color.get_color_names``.
        Can also be a hex value if it starts with ``'#'`` as ``'#ff0000'``.
        If array-like, it must be an 1-dimensional array with 3 or 4 elements.
    alpha : float | None
        If no alpha is not supplied in ``color`` entry and ``alpha`` is None,
        then this will default to 1.0 (opaque). If float, it will override
        the alpha value in ``color``, if provided.
    """
    def __init__(self, color='black', alpha=None, clip=False):
        """Parse input type, and set attribute"""
        if isinstance(color, (list, tuple)):
            color = np.array(color, np.float32)
        rgba = _user_to_rgba(color, clip=clip)
        if rgba.shape[0] != 1:
            raise ValueError('color must be of correct shape')
        if alpha is not None:
            rgba[:, 3] = alpha
        self._rgba = None
        self.rgba = rgba.ravel()

    @ColorArray.rgba.getter
    def rgba(self):
        return super(Color, self).rgba[0]

    @ColorArray.rgb.getter
    def rgb(self):
        return super(Color, self).rgb[0]

    @ColorArray.RGBA.getter
    def RGBA(self):
        return super(Color, self).RGBA[0]

    @ColorArray.RGB.getter
    def RGB(self):
        return super(Color, self).RGB[0]

    @ColorArray.alpha.getter
    def alpha(self):
        return super(Color, self).alpha[0]

    @ColorArray.hex.getter
    def hex(self):
        return super(Color, self).hex[0]

    @ColorArray.hsv.getter
    def hsv(self):
        return super(Color, self).hsv[0]

    @ColorArray.value.getter
    def value(self):
        return super(Color, self).value[0]

    @ColorArray.lab.getter
    def lab(self):
        return super(Color, self).lab[0]

    def is_blank(self):
        return self.rgba[3] == 0

    def __repr__(self):
        nice_str = str(tuple(self._rgba[0]))
        return ('<%s: %s>' % (self._name(), nice_str))


###############################################################################
# Color maps


# Utility functions for interpolation in NumPy.
def _vector_or_scalar(x, type='row'):
    """Convert an object to either a scalar or a row or column vector."""
    if isinstance(x, (list, tuple)):
        x = np.array(x)
    if isinstance(x, np.ndarray):
        assert x.ndim == 1
        if type == 'column':
            x = x[:, None]
    return x


def _vector(x, type='row'):
    """Convert an object to a row or column vector."""
    if isinstance(x, (list, tuple)):
        x = np.array(x, dtype=np.float32)
    elif not isinstance(x, np.ndarray):
        x = np.array([x], dtype=np.float32)
    assert x.ndim == 1
    if type == 'column':
        x = x[:, None]
    return x


def _find_controls(x, controls=None):
    n = len(controls)
    x_controls = np.clip(np.searchsorted(controls, x) - 1, 0, n-1)
    return x_controls


# Interpolation functions in NumPy.
def _mix_simple(a, b, x):
    """Mix b (with proportion x) with a."""
    x = np.clip(x, 0.0, 1.0)
    return (1.0 - x)*a + x*b


def _smoothstep_simple(a, b, x):
    y = x * x * (3. - 2. * x)
    return _mix_simple(a, b, y)


def _interpolate_multi(colors, x, controls):
    x = x.ravel()
    n = len(colors)
    # For each element in x, the control index of its bin's left boundary.
    x_step = np.clip(_find_controls(x, controls), 0, n-2)
    # The length of each bin.
    controls_length = np.diff(controls)
    # Prevent division by zero error.
    controls_length[controls_length == 0.] = 1.
    # Like x, but relative to each bin.
    x_rel = np.clip(((x - controls[x_step]) / controls_length[x_step]), 0, 1)
    return (colors[x_step],
            colors[x_step + 1],
            x_rel[:, None])


def mix(colors, x, controls=None):
    a, b, x_rel = _interpolate_multi(colors, x, controls)
    return _mix_simple(a, b, x_rel)


def smoothstep(colors, x, controls=None):
    a, b, x_rel = _interpolate_multi(colors, x, controls)
    return _smoothstep_simple(a, b, x_rel)


def step(colors, x, controls=None):
    """Step interpolation from a set of colors. x belongs in [0, 1]."""
    assert (controls[0], controls[-1]) == (0., 1.)
    ncolors = len(controls)
    assert ncolors >= 3
    x_step = _find_controls(x, controls)
    return colors[x_step, ...]


# GLSL interpolation functions.
def _glsl_mix(controls=None):
    """Generate a GLSL template function from a given interpolation patterns
    and control points."""
    if controls is None:
        controls = [0., 1.]
    assert (controls[0], controls[-1]) == (0., 1.)
    ncolors = len(controls)
    assert ncolors >= 2
    if ncolors == 2:
        s = "    return mix($color_0, $color_1, t);\n"
    else:
        s = ""
        for i in range(ncolors-1):
            if i == 0:
                ifs = 'if (t < %.6f)' % (controls[i+1])
            elif i == (ncolors-2):
                ifs = 'else'
            else:
                ifs = 'else if (t < %.6f)' % (controls[i+1])
            s += "%s {\n    return mix($color_%d, $color_%d, t);\n} " % \
                 (ifs, i, i+1)
    return "vec4 colormap(float t) {\n%s\n}" % s


def _glsl_step(controls=None):
    if controls is None:
        controls = [0., .5, 1.]
    assert (controls[0], controls[-1]) == (0., 1.)
    ncolors = len(controls)
    assert ncolors >= 3
    s = ""
    for i in range(ncolors-1):
        if i == 0:
            ifs = 'if (t < %.6f)' % (controls[i+1])
        elif i == (ncolors-2):
            ifs = 'else'
        else:
            ifs = 'else if (t < %.6f)' % (controls[i+1])
        s += """%s {\n    return $color_%d;\n} """ % (ifs, i)
    return """vec4 colormap(float t) {\n%s\n}""" % s


# Mini GLSL template system for colors.
def _process_glsl_template(template, colors):
    """Replace $color_i by color #i in the GLSL template."""
    for i in range(len(colors)):
        color = colors[i]
        assert len(color) == 4
        vec4_color = 'vec4(%.3f, %.3f, %.3f, %.3f)' % tuple(color)
        template = template.replace('$color_%d' % i, vec4_color)
    return template


class Colormap(object):
    """Class representing a colormap:

        t \in [0, 1] --> rgba_color

    Must be overriden. Child classes need to implement:

    colors : list of lists, tuples, or ndarrays
        The control colors used by the colormap (shape = (ncolors, 4)).
    glsl_map : string
        The GLSL function for the colormap. Use $color_0 to refer
        to the first color in `colors`, and so on. These are vec4 vectors.
    map(item) : function
        Takes a (N, 1) vector of values in [0, 1], and returns a rgba array
        of size (N, 4).

    """

    # Control colors used by the colormap.
    colors = None

    # GLSL string with a function implementing the color map.
    glsl_map = None

    def __init__(self, colors=None):
        if colors is not None:
            self.colors = colors
        if self.colors is None:
            self.colors = []
        # Ensure the colors are arrays.
        if isinstance(self.colors, ColorArray):
            self.colors = self.colors.rgba
        else:
            self.colors = np.array(self.colors, dtype=np.float32)
        # Process the GLSL map function by replacing $color_i by the
        if self.colors.size > 0:
            self.glsl_map = _process_glsl_template(self.glsl_map, self.colors)

    def map(self, item):
        """Return a rgba array for the requested items.

        This function must be overriden by child classes.

        This function doesn't need to implement argument checking on `item`.
        It can always assume that `item` is a (N, 1) array of values between
        0 and 1.

        Parameters
        ----------
        item : ndarray
            An array of values in [0,1]. Expected to be a column vector.

        Returns
        -------
        rgba : ndarray
            A (N, 4) array with rgba values, where N is `len(item)`.

        """
        raise NotImplementedError()

    def __getitem__(self, item):
        if isinstance(item, tuple):
            raise ValueError('ColorArray indexing is only allowed along '
                             'the first dimension.')
        # Ensure item is either a scalar or a column vector.
        item = _vector(item, type='column')
        colors = self.map(item)
        return ColorArray(colors)

    def __setitem__(self, item, value):
        raise RuntimeError("It is not possible to set items to "
                           "Colormap instances.")

    def _repr_html_(self):
        n = 500
        html = ("""
                <table style="width: 500px;
                              height: 50px;
                              border: 0;
                              margin: 0;
                              padding: 0;">
                """ +
                '\n'.join([(('<td style="background-color: %s; border: 0; '
                             'width: 1px; margin: 0; padding: 0;"></td>') %
                            _rgb_to_hex(color)[0])
                           for color in self[np.linspace(0., 1., n)].rgb]) +
                """
                </table>
                """)
        return html


def _default_controls(colors):
    """Generate linearly spaced control points from a set of colors."""
    n = len(colors)
    return np.linspace(0., 1., n)


class LinearGradient(Colormap):
    """A linear gradient with an arbitrary number of colors and control
    points in [0,1]."""
    def __init__(self, colors, controls=None):
        # Default controls.
        if controls is None:
            controls = _default_controls(colors)
        self.controls = controls
        # Generate the GLSL map.
        self.glsl_map = _glsl_mix(controls)
        super(LinearGradient, self).__init__(colors)

    def map(self, x):
        return mix(self.colors, x, self.controls)


class DiscreteColormap(Colormap):
    """A discrete colormap with an arbitrary number of colors and control
    points in [0,1]."""
    def __init__(self, colors, controls=None):
        # Default controls.
        if controls is None:
            controls = _default_controls(colors)
        self.controls = controls
        # Generate the GLSL map.
        self.glsl_map = _glsl_step(controls)
        super(DiscreteColormap, self).__init__(colors)

    def map(self, x):
        return step(self.colors, x, self.controls)


class Fire(Colormap):
    colors = [(1.0, 1.0, 1.0, 1.0),
              (1.0, 1.0, 0.0, 1.0),
              (1.0, 0.0, 0.0, 1.0)]

    glsl_map = """
    vec4 fire(float t) {
        return mix(mix($color_0, $color_1, t),
                   mix($color_1, $color_2, t*t), t);
    }
    """

    def map(self, t):
        a, b, d = self.colors
        c = _mix_simple(a, b, t)
        e = _mix_simple(b, d, t**2)
        return _mix_simple(c, e, t)


class Grays(Colormap):
    glsl_map = """
    vec4 grays(float t) {
        return vec4(t, t, t, 1.0);
    }
    """

    def map(self, t):
        if isinstance(t, np.ndarray):
            return np.hstack([t, t, t, np.ones(t.shape)]).astype(np.float32)
        else:
            return np.array([t, t, t, 1.0], dtype=np.float32)


class Ice(Colormap):
    glsl_map = """
    vec4 ice(float t) {
        return vec4(t, t, 1.0, 1.0);
    }
    """

    def map(self, t):
        if isinstance(t, np.ndarray):
            return np.hstack([t, t, np.ones(t.shape),
                              np.ones(t.shape)]).astype(np.float32)
        else:
            return np.array([t, t, 1.0, 1.0], dtype=np.float32)


class Hot(Colormap):
    colors = [(0., .33, .66, 1.0),
              (.33, .66, 1., 1.0)]

    glsl_map = """
    vec4 hot(float t) {
        return vec4(smoothstep($color_0.rgb, $color_1.rgb, vec3(t, t, t)),
                    1.0);
    }
    """

    def map(self, t):
        n = len(self.colors)
        return np.hstack((_smoothstep_simple(self.colors[0, :3],
                                             self.colors[1, :3],
                                             t),
                         np.ones((n, 1))))


class Winter(Colormap):
    colors = [(0.0, 0.0, 1.0, 1.0),
              (0.0, 1.0, 0.5, 1.0)]

    glsl_map = """
    vec4 winter(float t) {
        return mix($color_0, $color_1, sqrt(t));
    }
    """

    def map(self, t):
        return _mix_simple(self.colors[0], self.colors[1], np.sqrt(t))


_colormaps = dict(
    autumn=LinearGradient([(1., 0., 0., 1.), (1., 1., 0., 1.)]),
    blues=LinearGradient([(1., 1., 1., 1.), (0., 0., 1., 1.)]),
    cool=LinearGradient([(0., 1., 1., 1.), (1., 0., 1., 1.)]),
    greens=LinearGradient([(1., 1., 1., 1.), (0., 1., 0., 1.)]),
    reds=LinearGradient([(1., 1., 1., 1.), (1., 0., 0., 1.)]),
    spring=LinearGradient([(1., 0., 1., 1.), (1., 1., 0., 1.)]),
    summer=LinearGradient([(0., .5, .4, 1.), (1., 1., .4, 1.)]),
    fire=Fire(),
    grays=Grays(),
    hot=Hot(),
    ice=Ice(),
    winter=Winter(),
)


def get_colormap(name):
    """Return a Colormap instance given its name."""
    return _colormaps[name]


def get_colormaps():
    """Return the list of colormap names."""
    return list(sorted(_colormaps.keys()))
