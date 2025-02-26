import torch
import torch.distributions as dist
import torch.utils.data as data_utils

from .sgd_gmm import SGDGMMModule, BaseSGDGMM

from ..utils.sampling import minibatch_sample

mvn = dist.multivariate_normal.MultivariateNormal


class SGDDeconvGMMModule(SGDGMMModule):

    def forward(self, data):
        x, noise_covars = data

        weights = self.soft_max(self.soft_weights)

        T = self.covars[None, :, :, :] + noise_covars[:, None, :, :]

        log_resp = mvn(loc=self.means, covariance_matrix=T).log_prob(
            x[:, None, :]
        )
        log_resp += torch.log(weights)

        log_prob = torch.logsumexp(log_resp, dim=1)

        return log_prob


class SGDDeconvDataset(data_utils.Dataset):

    def __init__(self, X, noise_covars):
        self.X = X
        self.noise_covars = noise_covars

    def __len__(self):
        return self.X.shape[0]

    def __getitem__(self, i):
        return (self.X[i, :], self.noise_covars[i, :, :])


class SGDDeconvGMM(BaseSGDGMM):

    def __init__(self, components, dimensions, epochs=10000, lr=1e-3,
                 batch_size=64, tol=1e-6, w=1e-3,
                 k_means_factor=100, k_means_iters=10, lr_step=5,
                 lr_gamma=0.1, device=None):
        self.module = SGDDeconvGMMModule(components, dimensions, w, device)
        super().__init__(
            components, dimensions, epochs=epochs, lr=lr,
            batch_size=batch_size, w=w, tol=tol,
            k_means_factor=k_means_factor, k_means_iters=k_means_iters,
            lr_step=lr_step, lr_gamma=lr_gamma, device=device
        )
        
    def _sample_prior(self, num_samples, context=None):
        return self._sample(num_samples)
    
    def sample_prior(self, num_samples, device=torch.device('cpu')):
        with torch.no_grad():
             return minibatch_sample(
                self._sample_prior,
                num_samples,
                self.d,
                self.batch_size,
                device
            )
            
    def posterior_params(self, x):
        log_weights = torch.log(self.module.soft_max(self.module.soft_weights))
        T = self.module.covars[None, :, :, :] + x[1][:, None, :, :]
        
        w = log_weights + dist.MultivariateNormal(
            loc=self.module.means, covariance_matrix=T
        ).log_prob(x[0][:, None, :])
        p_weights = w - torch.logsumexp(w, axis=1)[:, None]
        
        L_t = torch.linalg.cholesky(T)
        T_inv = torch.cholesky_solve(torch.eye(self.d, device=self.device), L_t)
        
        diff = x[0][:, None, :] - self.module.means
        T_prod = torch.matmul(T_inv, diff[:, :, :, None])
        p_means = self.module.means + torch.matmul(
            self.module.covars,
            T_prod
        ).squeeze()
        
        p_covars = self.module.covars - torch.matmul(
            self.module.covars,
            torch.matmul(T_inv, self.module.covars)
        )
        return p_weights, p_means, p_covars
    
    def posterior_log_prob(self, x, context):
        p_weights, p_means, p_covars = self.posterior_params(context)
        if len(x.shape) == 2:
            log_p = dist.MultivariateNormal(loc=p_means, covariance_matrix=p_covars).log_prob(x[:, None, None, :])
            return torch.logsumexp(log_p + p_weights, dim=2)
        else:
            log_p = dist.MultivariateNormal(
                loc=p_means, covariance_matrix=p_covars
            ).log_prob(x.transpose(0, 1)[:, :, None, :])
            return torch.logsumexp(log_p + p_weights, dim=2).transpose(0, 1)

    def _sample_posterior(self, x, num_samples, context=None):
        
        p_weights, p_means, p_covars = self.posterior_params(x)
        
        idx = dist.Categorical(logits=p_weights).sample([num_samples])
        samples = dist.MultivariateNormal(loc=p_means, covariance_matrix=p_covars).sample([num_samples])
        
        return samples.transpose(0, 1)[
            torch.arange(len(x[0]), device=self.device)[:, None, None, None],
            torch.arange(num_samples, device=self.device)[None, :, None, None],
            idx.T[:, :, None, None],
            torch.arange(self.d, device=self.device)[None, None, None, :]
        ].squeeze()
    
    def sample_posterior(self, x, num_samples, device=torch.device('cpu')):
        with torch.no_grad():
            return minibatch_sample(
                self._sample_posterior,
                num_samples,
                self.d,
                self.batch_size,
                device,
                x=x
            )
                                                          
                                                    
                                                          
        
