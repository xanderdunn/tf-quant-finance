# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# Lint as: python2, python3
"""Utility functions needed for brownian motion and related processes."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf
from tf_quant_finance.math import gradient


def is_callable(var_or_fn):
  """Returns whether an object is callable or not."""
  # Python 2.7 as well as Python 3.x with x > 2 support 'callable'.
  # In between, callable was removed hence we need to do a more expansive check
  if hasattr(var_or_fn, '__call__'):
    return True
  try:
    return callable(var_or_fn)
  except NameError:
    return False


def outer_multiply(x, y):
  """Performs an outer multiplication of two tensors.

  # TODO: Should this function be moved to a more visible location?

  Given two `Tensor`s, `S` and `T` of shape `s` and `t` respectively, the outer
  product `P` is a `Tensor` of shape `s + t` whose components are given by:

  ```none
  P_{i1,...ik, j1, ... , jm} = S_{i1...ik} T_{j1, ... jm}
  ```

  Args:
    x: A `Tensor` of any shape and numeric dtype.
    y: A `Tensor` of any shape and the same dtype as `x`.

  Returns:
    outer_product: A `Tensor` of shape Shape[x] + Shape[y] and the same dtype
      as `x`.
  """
  x_shape = tf.shape(x)
  padded_shape = tf.concat(
      [x_shape, tf.ones(tf.rank(y), dtype=x_shape.dtype)], axis=0)
  return tf.reshape(x, padded_shape) * y


def construct_drift_data(drift, total_drift_fn, dim, dtype):
  """Constructs drift functions."""
  # Six cases based on drift being (None, callable, constant) and total drift
  # being (None, callable).
  # 1. Neither drift nor total drift given -> return defaults.
  # 2. total drift is none and drift is a callable -> raise error.
  # 3. total drift is none and drift is a constant -> calculate.
  # 4. total drift is given and drift is none -> differentiate.
  # 5. total drift is given and drift is callable -> return them.
  # 6. total drift is given and drift is constant -> wrap and return.

  # Case 1, 2 and 3.
  if total_drift_fn is None:
    # Case 1
    if drift is None:
      return _default_drift_data(dim, dtype)
    if is_callable(drift):
      # Case 2: drift is a function and total_drift_fn needs to be computed.
      # We need numerical integration for this which will be added later.
      # TODO(b/141091950): Add numerical integration.
      raise ValueError('Total drift function cannot be inferred from drift.')
    # Case 3. Drift is a constant.
    def total_drift(t1, t2):
      return outer_multiply(t2 - t1, tf.ones([dim], dtype=dtype) * drift)

    return _make_drift_fn_from_const(drift, dim, dtype), total_drift

  # Total drift is not None
  # Case 4.
  if drift is None:

    def drift_from_total_drift(t):
      start_time = tf.zeros_like(t)
      return gradient.fwd_gradient(lambda x: total_drift_fn(start_time, x), t)

    return drift_from_total_drift, total_drift_fn

  # Case 5
  if is_callable(drift):
    return drift, total_drift_fn

  return _make_drift_fn_from_const(drift, dim, dtype), total_drift_fn


def construct_vol_data(volatility, total_covariance_fn, dim, dtype):
  """Constructs volatility data.

  This function resolves the supplied arguments in to the following ten cases:
  (vol -> volatility, total_covar -> total_covariance_fn)
  1. vol and total_covar are both None -> Return default values.
  2. total_covar is supplied and vol is None -> compute vol from total covar.
  3. total_covar is supplied and vol is a callable -> Return supplied values.
  4. total_covar is supplied and vol is a scalar constant.
  5. total_covar is supplied and vol is a vector constant.
  6. total_covar is supplied and vol is a matrix constant.
  7. total_covar is not supplied and vol is a callable -> Raise error (numerical
    integration is not supported yet).
  8. total_covar is not supplied and vol is a scalar constant.
  9. total_covar is not supplied and vol is a vector constant.
  10. total_covar is not supplied and vol is a matrix.

  For cases 4, 5 and 6 we create an appropriate volatility fn. For cases 8
  through to 10 we do the same but also create an appropriate covariance
  function.

  Args:
    volatility: The volatility specification. None or a callable or a scalar,
      vector or matrix.
    total_covariance_fn: The total covariance function. Either None or a
      callable.
    dim: int. The dimension of the process.
    dtype: The default dtype to use.

  Returns:
    A tuple of two callables:
      volatility_fn: A function accepting a time argument and returning
        the volatility at that time.
      total_covariance_fn: A function accepting two time arguments and
        returning the total covariance between the two times.
  """
  # Case 1
  if volatility is None and total_covariance_fn is None:
    return _default_vol_data(dim, dtype)

  if total_covariance_fn is not None:
    # Case 2
    if volatility is None:
      vol_fn = _volatility_fn_from_total_covar_fn(total_covariance_fn)
      return vol_fn, total_covariance_fn
    # Case 3
    if is_callable(volatility):
      return volatility, total_covariance_fn
    # Cases 4, 5, 6
    return _construct_vol_data_const_vol(volatility, total_covariance_fn, dim,
                                         dtype)

  # Case 7.
  if is_callable(volatility):
    # TODO(b/141091950): Add numerical integration.
    raise ValueError('Total covariance fn cannot be inferred from vol fn.')

  # Cases 8-10
  return _construct_vol_data_const_vol(volatility, None, dim, dtype)


def _make_drift_fn_from_const(drift_const, dim, dtype):
  drift_const = tf.ones([dim], dtype=dtype) * drift_const
  return lambda t: outer_multiply(tf.ones_like(t), drift_const)


def _volatility_fn_from_total_covar_fn(total_covariance_fn):
  """Volatility function from total covariance function."""

  def vol_fn(time):
    # We should consider changing the start time to be some small dt behind
    # the time. In case the total covariance is being computed by a numerical
    # integration, this will mean that we spend less time iterating.
    start_time = tf.zeros_like(time)
    total_covar_fn = lambda t: total_covariance_fn(start_time, t)
    vol_sq = gradient.fwd_gradient(total_covar_fn, time)
    return tf.cholesky(vol_sq, name='volatility')

  return vol_fn


def _default_drift_data(dimension, dtype):
  """Constructs a function which returns a zero drift."""

  def zero_drift(time):
    return tf.zeros(
        tf.concat([tf.shape(time), [dimension]], axis=0), dtype=dtype)

  return zero_drift, (lambda t1, t2: zero_drift(t1))


def _default_vol_data(dimension, dtype):
  """Unit volatility and corresponding covariance functions."""
  identity = tf.eye(dimension, dtype=dtype, name='identity_vol')
  unit_vol = lambda time: outer_multiply(tf.ones_like(time), identity)

  def covar(start_time, end_time):
    return outer_multiply(end_time - start_time, identity)

  return unit_vol, covar


def _ensure_matrix(volatility, dim, dtype):
  """Converts a volatility tensor to the right shape."""
  # Works only for static rank.
  rank = len(volatility.shape)
  if not rank:
    return tf.eye(dim, dtype=dtype) * volatility
  if rank == 1:
    return tf.linalg.tensor_diag(volatility)
  # It is of rank 2 at least
  return volatility


def _covar_from_vol(volatility, dim, dtype):
  rank = len(volatility.shape)
  if not rank:
    return volatility * volatility * tf.eye(dim, dtype=dtype)
  if rank == 1:
    return tf.linalg.tensor_diag(volatility * volatility)
  return tf.linalg.matmul(volatility, volatility, transpose_b=True)


def _construct_vol_data_const_vol(volatility, total_covariance_fn, dim, dtype):
  """Constructs vol data when constant volatility is supplied."""
  volatility_matrix = _ensure_matrix(volatility, dim, dtype)

  def vol_fn(time):
    return outer_multiply(tf.ones_like(time), volatility_matrix)

  if total_covariance_fn is not None:
    return vol_fn, total_covariance_fn

  # Need to compute total covariance.
  covariance_matrix = _covar_from_vol(volatility, dim, dtype)
  covar_fn = lambda t1, t2: outer_multiply(t2 - t1, covariance_matrix)
  return vol_fn, covar_fn
