import torch
import torch.nn.functional as F

import nsflow as nf
from nsflow import nde as nde

from .base import BaseFlow

class AffineCouplingFlow(BaseFlow):

    def _create_acf_transform(self, step, context_features):
        return nf.nde.transforms.AffineCouplingTransform(
            mask=nf.utils.create_alternating_binary_mask(
                features=self.dimensions,
                even=(step % 2 == 0)
            ),
            transform_net_create_fn=lambda i, o: nf.nn.ResidualNet(
                in_features=i,
                out_features=o,
                hidden_features=256,
                context_features=context_features,
                num_blocks=2,
                activation=F.relu,
                dropout_probability=0.2,
                use_batch_norm=False
            )
        )

    def _create_transform(self, context_features=None):

        return nde.transforms.CompositeTransform([

            nde.transforms.CompositeTransform([
                self._create_linear_transform(),
                self._create_acf_transform(i, context_features)
            ]) for i in range(self.flow_steps)
        ] + [
            self._create_linear_transform()
        ])
