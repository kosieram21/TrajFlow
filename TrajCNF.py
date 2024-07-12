import torch
import torch.nn as nn
import torch.nn.functional as F
from torchdiffeq import odeint_adjoint

import numpy as np # natural cubic spline uses this... can we just use torch? I think we can use torch.empty

class NaturalCubicSpline:
	"""Calculates the natural cubic spline approximation to the batch of controls given. Also calculates its derivative.

	Example:
		times = torch.linspace(0, 1, 7)
		# (2, 1) are batch dimensions. 7 is the time dimension (of the same length as t). 3 is the channel dimension.
		X = torch.rand(2, 1, 7, 3)
		coeffs = natural_cubic_spline_coeffs(times, X)
		# ...at this point you can save the coeffs, put them through PyTorch's Datasets and DataLoaders, etc...
		spline = NaturalCubicSpline(times, coeffs)
		t = torch.tensor(0.4)
		# will be a tensor of shape (2, 1, 3), corresponding to batch and channel dimensions
		out = spline.derivative(t)
	"""

	def __init__(self, times, path, **kwargs):
		"""
		Arguments:
			times: As was passed as an argument to natural_cubic_spline_coeffs.
			coeffs: As returned by natural_cubic_spline_coeffs.
		"""
		super(NaturalCubicSpline, self).__init__(**kwargs)

		# as we're typically computing derivatives, we store the multiples of these coefficients (c, d) that are more useful
		self._times = times
		a, b, two_c, three_d = self._coefficients(times, path.transpose(-1, -2))
		self._a = a.transpose(-1, -2)
		self._b = b.transpose(-1, -2)
		self._two_c = two_c.transpose(-1, -2)
		self._three_d = three_d.transpose(-1, -2)
		
	def _coefficients(self, times, path):
		# path should be a tensor of shape (..., length)
		# Will return the b, two_c, three_d coefficients of the derivative of the cubic spline interpolating the path.

		length = path.size(-1)

		if length < 2:
			# In practice this should always already be caught in __init__.
			raise ValueError("Must have a time dimension of size at least 2.")
		elif length == 2:
			a = path[..., :1]
			b = (path[..., 1:] - path[..., :1]) / (times[..., 1:] - times[..., :1])
			two_c = torch.zeros(*path.shape[:-1], 1, dtype=path.dtype, device=path.device)
			three_d = torch.zeros(*path.shape[:-1], 1, dtype=path.dtype, device=path.device)
		else:
			# Set up some intermediate values
			time_diffs = times[1:] - times[:-1]
			time_diffs_reciprocal = time_diffs.reciprocal()
			time_diffs_reciprocal_squared = time_diffs_reciprocal ** 2
			three_path_diffs = 3 * (path[..., 1:] - path[..., :-1])
			six_path_diffs = 2 * three_path_diffs
			path_diffs_scaled = three_path_diffs * time_diffs_reciprocal_squared

			# Solve a tridiagonal linear system to find the derivatives at the knots
			system_diagonal = torch.empty(length, dtype=path.dtype, device=path.device)
			system_diagonal[:-1] = time_diffs_reciprocal
			system_diagonal[-1] = 0
			system_diagonal[1:] += time_diffs_reciprocal
			system_diagonal *= 2
			system_rhs = torch.empty_like(path)
			system_rhs[..., :-1] = path_diffs_scaled
			system_rhs[..., -1] = 0
			system_rhs[..., 1:] += path_diffs_scaled
			knot_derivatives = self._tridiagonal_solve(system_rhs, time_diffs_reciprocal, system_diagonal,
												  time_diffs_reciprocal)

			# Do some algebra to find the coefficients of the spline
			a = path[..., :-1]
			b = knot_derivatives[..., :-1]
			two_c = (six_path_diffs * time_diffs_reciprocal
				- 4 * knot_derivatives[..., :-1]
				- 2 * knot_derivatives[..., 1:]) * time_diffs_reciprocal
			three_d = (-six_path_diffs * time_diffs_reciprocal
				+ 3 * (knot_derivatives[..., :-1]
					+ knot_derivatives[..., 1:])) * time_diffs_reciprocal_squared

		return a, b, two_c, three_d

	def _tridiagonal_solve(self, b, A_upper, A_diagonal, A_lower):
		"""Solves a tridiagonal system Ax = b.

		The arguments A_upper, A_digonal, A_lower correspond to the three diagonals of A. Letting U = A_upper, D=A_digonal
		and L = A_lower, and assuming for simplicity that there are no batch dimensions, then the matrix A is assumed to be
		of size (k, k), with entries:

		D[0] U[0]
		L[0] D[1] U[1]
			L[1] D[2] U[2]                     0
				L[2] D[3] U[3]
					.    .    .
						.      .      .
							.        .        .
								L[k - 3] D[k - 2] U[k - 2]
		   0                            L[k - 2] D[k - 1] U[k - 1]
											L[k - 1]   D[k]

		Arguments:
			b: A tensor of shape (..., k), where '...' is zero or more batch dimensions
			A_upper: A tensor of shape (..., k - 1).
			A_diagonal: A tensor of shape (..., k).
			A_lower: A tensor of shape (..., k - 1).

		Returns:
			A tensor of shape (..., k), corresponding to the x solving Ax = b

		Warning:
			This implementation isn't super fast. You probably want to cache the result, if possible.
		"""

		# This implementation is very much written for clarity rather than speed.
		A_upper, _ = torch.broadcast_tensors(A_upper, b[..., :-1])
		A_lower, _ = torch.broadcast_tensors(A_lower, b[..., :-1])
		A_diagonal, b = torch.broadcast_tensors(A_diagonal, b)

		channels = b.size(-1)

		new_b = np.empty(channels, dtype=object)
		new_A_diagonal = np.empty(channels, dtype=object)
		outs = np.empty(channels, dtype=object)

		new_b[0] = b[..., 0]
		new_A_diagonal[0] = A_diagonal[..., 0]
		for i in range(1, channels):
			w = A_lower[..., i - 1] / new_A_diagonal[i - 1]
			new_A_diagonal[i] = A_diagonal[..., i] - w * A_upper[..., i - 1]
			new_b[i] = b[..., i] - w * new_b[i - 1]

		outs[channels - 1] = new_b[channels - 1] / new_A_diagonal[channels - 1]
		for i in range(channels - 2, -1, -1):
			outs[i] = (new_b[i] - A_upper[..., i] * outs[i + 1]) / new_A_diagonal[i]

		return torch.stack(outs.tolist(), dim=-1)

	def _interpret_t(self, t):
		maxlen = self._b.size(-2) - 1
		index = (t > self._times).sum() - 1
		index = index.clamp(0, maxlen)  # clamp because t may go outside of [t[0], t[-1]]; this is fine
		# will never access the last element of self._times; this is correct behaviour
		fractional_part = t - self._times[index]
		return fractional_part, index

	def evaluate(self, t):
		"""Evaluates the natural cubic spline interpolation at a point t, which should be a scalar tensor."""
		fractional_part, index = self._interpret_t(t)
		inner = 0.5 * self._two_c[..., index, :] + self._three_d[..., index, :] * fractional_part / 3
		inner = self._b[..., index, :] + inner * fractional_part
		return self._a[..., index, :] + inner * fractional_part

	def derivative(self, t):
		"""Evaluates the derivative of the natural cubic spline at a point t, which should be a scalar tensor."""
		fractional_part, index = self._interpret_t(t)
		inner = self._two_c[..., index, :] + self._three_d[..., index, :] * fractional_part
		deriv = self._b[..., index, :] + inner * fractional_part
		return deriv
	
class MLP(nn.Module):
	def __init__(self, input_dim, output_dim, hidden_dims):
		super(MLP, self).__init__()
		dim_list = [input_dim ] + list(hidden_dims) + [output_dim]
		layers = []
		for i in range(len(dim_list) - 1):
			layers.append(nn.Linear(dim_list[i], dim_list[i + 1]))
			if i < len(dim_list) - 2:
				layers.append(nn.LayerNorm(dim_list[i + 1]))
				layers.append(nn.Softplus())
		self.mlp = nn.Sequential(*layers)

	def forward(self, x):
		return self.mlp(x)


class CDE(nn.Module):
	def __init__(self, input_dim, hidden_dim, num_layers=3): # TODO: use num_layers
		super(CDE, self).__init__()
		self.input_dim = input_dim
		self.hidden_dim = hidden_dim
		self.mlp = MLP(hidden_dim, input_dim * hidden_dim, (hidden_dim, hidden_dim))

		#self.linear1 = nn.Linear(hidden_dim, hidden_dim)
		#self.layer_norm1 = nn.LayerNorm(hidden_dim)
		#self.linear2 = nn.Linear(hidden_dim, 2 * hidden_dim)
		#self.linear3 = nn.Linear(2 * hidden_dim, 4 * hidden_dim)
		#self.linear4 = nn.Linear(hidden_dim, input_dim * hidden_dim)
	
	def forward(self, x):
		#x = self.linear1(x)
		#x = self.layer_norm1(x)
		#x = x.relu()
		#x = F.softplus(x)
		#x = self.linear2(x)
		#x = x.relu()
		#x = self.linear3(x)
		#x = x.relu()
		#x = self.linear4(x)
		#x = x.tanh()
		x = self.mlp(x)
		x = x.tanh()
		x = x.view(*x.shape[:-1], self.hidden_dim, self.input_dim)
		return x
	

class VectorField(torch.nn.Module):
	def __init__(self, dX_dt, f):
		super(VectorField, self).__init__()
		self.dX_dt = dX_dt.derivative
		self.X = dX_dt.evaluate
		self.f = f

	def forward(self, t, z):
		dX_dt = self.dX_dt(t)
		f = self.f(z)
		out = (f @ dX_dt.unsqueeze(-1)).squeeze(-1)
		return out
	

class CasualEncoder(torch.nn.Module):
	def __init__(self, input_dim, hidden_dim):
		super(CasualEncoder, self).__init__()
		self.embed = MLP(input_dim, hidden_dim, (hidden_dim, hidden_dim))#torch.nn.Linear(input_dim, hidden_dim)
		self.readout = MLP(hidden_dim, hidden_dim, (hidden_dim, hidden_dim))
		self.f = CDE(input_dim, hidden_dim)

	def forward(self, t, x):
		spline = NaturalCubicSpline(t, x)
		vector_field = VectorField(dX_dt=spline, f=self.f)
		z0 = self.embed(spline.evaluate(t[0]))
		out = odeint_adjoint(vector_field, z0, t, method='dopri5', atol=1e-5, rtol=1e-5)
		embedding = self.readout(out[1])
		return embedding


class ConditionalODE(nn.Module):
	def __init__(self, input_dim, condition_dim, hidden_dims):
		super(ConditionalODE, self).__init__()
		dim_list = [input_dim + condition_dim] + list(hidden_dims) + [input_dim]
		layers = []
		for i in range(len(dim_list) - 1):
			layers.append(nn.Linear(dim_list[i] + 2, dim_list[i + 1]))
			if i < len(dim_list) - 2:
				layers.append(nn.LayerNorm(dim_list[i + 1]))
		self.layers = nn.ModuleList(layers)
		self.condition = None

	def _z_dot(self, t, z):
		positional_encoding = (torch.cumsum(torch.ones_like(z)[:, :, 0], 1) / z.shape[1]).unsqueeze(-1)
		time_encoding = t.expand(z.shape[0], z.shape[1], 1)
		condition = self.condition.unsqueeze(1).expand(-1, z.shape[1], -1)
		z_dot = torch.cat([z, condition], dim=-1)
		for i in range(0, len(self.layers), 2):
		#for i in range(len(self.layers)):
			tpz_cat = torch.cat([time_encoding, positional_encoding, z_dot], dim=-1)
			z_dot = self.layers[i](tpz_cat)
			if i < len(self.layers) - 2:
				z_dot = self.layers[i + 1](z_dot)
				z_dot = F.softplus(z_dot)
		return z_dot
	
	def _jacobian_trace(seld, z_dot, z):
		batch_size, seq_len, dim = z.shape
		trace = torch.zeros(batch_size, seq_len, device=z.device)
		for i in range(dim):
			trace += torch.autograd.grad(z_dot[:, :, i].sum(), z, create_graph=True)[0][:, :, i]
		return trace
	
	def forward(self, t, states):
		z = states[0]

		with torch.set_grad_enabled(True):
			z.requires_grad_(True)
			t.requires_grad_(True)
			z_dot = self._z_dot(t, z)
			divergence = self._jacobian_trace(z_dot, z)

		return z_dot, -divergence


class ConditionalCNF(torch.nn.Module):
	def __init__(self, input_dim, condition_dim, hidden_dims):
		super(ConditionalCNF, self).__init__()
		self.time_derivative = ConditionalODE(input_dim, condition_dim, hidden_dims)

	def forward(self, z, condition, delta_logpz=None, integration_times=None, reverse=False):
		if delta_logpz is None:
			delta_logpz = torch.zeros(z.shape[0], z.shape[1]).to(z)
		if integration_times is None:
			integration_times = torch.tensor([0.0, 1.0]).to(z)
		if reverse:
			integration_times = torch.flip(integration_times, [0])

		self.time_derivative.condition = condition
		state = odeint_adjoint(self.time_derivative, (z, delta_logpz), integration_times, method='dopri5', atol=1e-5, rtol=1e-5)

		if len(integration_times) == 2:
			state = tuple(s[1] for s in state)
		z, delta_logpz = state
		return z, delta_logpz
	

class TrajCNF(torch.nn.Module):
	def __init__(self, seq_len, input_dim, feature_dim, embedding_dim, hidden_dims):
		super(TrajCNF, self).__init__()
		self.causal_encoder = nn.GRU(input_dim + feature_dim, embedding_dim, num_layers=3, batch_first=True)
		#self.causal_encoder = CasualEncoder(input_dim + feature_dim, embedding_dim)
		self.flow = ConditionalCNF(input_dim, embedding_dim, hidden_dims)

		self.register_buffer("base_dist_mean", torch.zeros(seq_len, input_dim))
		self.register_buffer("base_dist_var", torch.ones(seq_len, input_dim))

	@property
	def _base_dist(self):
		return torch.distributions.MultivariateNormal(self.base_dist_mean, torch.diag_embed(self.base_dist_var))

	def _embedding(self, x, feat):
		x = torch.cat([x, feat], dim=-1)
		embedding, _ = self.causal_encoder(x)
		return embedding[:, -1, :]
		#_, seq_length, _ = x.shape
		#indices = torch.linspace(0., 1., seq_length).to(x)
		#embedding = self.causal_encoder(indices, x)
		#return embedding

	def forward(self, x, y, feat):
		embedding = self._embedding(x, feat)
		z, delta_logpz = self.flow(y, embedding)
		return z, delta_logpz
	
	def sample(self, x, feat, num_samples=1):
		y = torch.stack([self._base_dist.sample().to(x.device) for _ in range(num_samples)])
		embedding = self._embedding(x, feat)
		z, delta_logpz = self.flow(y, embedding, reverse=True)
		return y, z, delta_logpz

	def log_prob(self, z_t0, delta_logpz):
		logpz_t0 = self._base_dist.log_prob(z_t0)
		logpz_t1 = logpz_t0 - delta_logpz
		return logpz_t0, logpz_t1
	