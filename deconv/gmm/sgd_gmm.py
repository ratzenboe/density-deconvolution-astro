from abc import ABC
import copy

import torch
import torch.distributions as dist
import torch.nn as nn
import torch.utils.data as data_utils

from .util import minibatch_k_means

mvn = dist.multivariate_normal.MultivariateNormal


class SGDGMMModule(nn.Module):

    def __init__(self, components, dimensions, device=None):
        super().__init__()

        self.k = components
        self.d = dimensions
        self.device = device

        self.soft_weights = nn.Parameter(torch.zeros(self.k))
        self.soft_max = torch.nn.Softmax(dim=0)

        self.means = nn.Parameter(torch.rand(self.k, self.d))
        self.l_diag = nn.Parameter(torch.zeros(self.k, self.d))

        self.l_lower = nn.Parameter(
            torch.zeros(self.k, self.d * (self.d - 1) // 2)
        )

        self.d_idx = torch.eye(self.d, device=self.device).to(torch.bool)
        self.l_idx = torch.tril_indices(self.d, self.d, -1, device=self.device)

    @property
    def L(self):
        L = torch.zeros(self.k, self.d, self.d, device=self.device)
        L[:, self.d_idx] = torch.exp(self.l_diag)
        L[:, self.l_idx[0], self.l_idx[1]] = self.l_lower
        return L

    @property
    def covars(self):
        return torch.matmul(self.L, torch.transpose(self.L, -2, -1))

    def forward(self, data):

        x = data[0]

        weights = self.soft_max(self.soft_weights)

        log_resp = mvn(loc=self.means, scale_tril=self.L).log_prob(
            x[:, None, :]
        )
        log_resp += torch.log(weights)

        log_prob = torch.logsumexp(log_resp, dim=1)

        return -1 * torch.sum(log_prob)


class BaseSGDGMM(ABC):

    def __init__(self, components, dimensions, epochs=10000, lr=1e-3,
                 batch_size=64, tol=1e-6, restarts=5, max_no_improvement=20,
                 k_means_factor=100, k_means_iters=10, device=None):
        self.k = components
        self.d = dimensions
        self.epochs = epochs
        self.batch_size = batch_size
        self.tol = 1e-6
        self.lr = lr
        self.restarts = restarts
        self.k_means_factor = k_means_factor
        self.k_means_iters = k_means_iters
        self.max_no_improvement = max_no_improvement

        if not device:
            self.device = torch.device('cpu')
        else:
            self.device = device

        self.module.to(device)

        self.optimiser = torch.optim.Adam(
            params=self.module.parameters(),
            lr=self.lr
        )

    @property
    def means(self):
        return self.module.means.detach()

    @property
    def covars(self):
        return self.module.covars.detach()

    def fit(self, data, val_data=None, verbose=False, interval=1):

        init_loader = data_utils.DataLoader(
            data,
            batch_size=self.batch_size * self.k_means_factor,
            num_workers=4,
            shuffle=True,
            pin_memory=True
        )

        loader = data_utils.DataLoader(
            data,
            batch_size=self.batch_size,
            num_workers=4,
            shuffle=True,
            pin_memory=True
        )

        best_loss = float('inf')

        for j in range(self.restarts):

            self.init_params(init_loader)

            train_loss_curve = []

            if val_data:
                val_loss_curve = []

            prev_loss = float('inf')
            if val_data:
                best_val_loss = float('inf')
                no_improvement_epochs = 0

            for i in range(self.epochs):
                train_loss = 0.0
                for j, d in enumerate(loader):

                    d = [a.to(self.device) for a in d]

                    self.optimiser.zero_grad()

                    loss = self.module(d)
                    loss.backward()
                    self.optimiser.step()

                    train_loss += loss.item()

                train_loss_curve.append(train_loss)

                if val_data:
                    val_loss = self.score_batch(val_data)
                    val_loss_curve.append(val_loss)

                if verbose and i % interval == 0:
                    if val_data:
                        print('Epoch {}, Train Loss: {}, Val Loss :{}'.format(
                            i,
                            train_loss,
                            val_loss
                        ))
                    else:
                        print('Epoch {}, Loss: {}'.format(i, train_loss))

                if val_data:
                    if val_loss < best_val_loss:
                        no_improvement_epochs = 0
                        best_val_loss = val_loss
                    else:
                        no_improvement_epochs += 1

                    if no_improvement_epochs > self.max_no_improvement:
                        print('No improvement in val loss for {} epochs. Early Stopping at {}'.format(
                            self.max_no_improvement,
                            val_loss
                        ))
                        break

                if abs(train_loss - prev_loss) < self.tol:
                    print('Training loss converged within tolerance at {}'.format(
                        train_loss
                    ))
                    break

                prev_loss = train_loss

            if val_data:
                score = val_loss
            else:
                score = train_loss

            if score < best_loss:
                best_model = copy.deepcopy(self.module)
                best_loss = score
                best_train_loss_curve = train_loss_curve
                if val_data:
                    best_val_loss_curve = val_loss_curve

        self.module = best_model
        self.train_loss_curve = best_train_loss_curve
        if val_data:
            self.val_loss_curve = best_val_loss_curve

    def score(self, data):
        with torch.no_grad():
            return self.module(data)

    def score_batch(self, dataset):
        loader = data_utils.DataLoader(
            dataset,
            batch_size=self.batch_size,
            num_workers=4,
            pin_memory=True
        )

        log_prob = 0

        for j, d in enumerate(loader):
            d = [a.to(self.device) for a in d]
            log_prob += self.score(d).item()

        return log_prob

    def init_params(self, loader):
        counts, centroids = minibatch_k_means(loader, self.k, max_iters=self.k_means_iters, device=self.device)
        self.module.soft_weights.data = torch.log(counts / counts.sum())
        self.module.means.data = centroids
        self.module.l_diag.data = nn.Parameter(torch.zeros(self.k, self.d, device=self.device))
        self.module.l_lower.data = torch.zeros(self.k, self.d * (self.d - 1) // 2, device=self.device)

class SGDGMM(BaseSGDGMM):

    def __init__(self, components, dimensions, epochs=10000, lr=1e-3,
                 batch_size=64, tol=1e-6, device=None):
        self.module = SGDGMMModule(components, dimensions, device)
        super().__init__(
            components, dimensions, epochs=epochs, lr=lr,
            batch_size=batch_size, tol=tol, device=device
        )
