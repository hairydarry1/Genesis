"""
model/world_model.py — Distributional world model for synthetic indices.

Architecture:
  - 3 timeframe encoders (1min, 15min, 1h) — each a small MLP
  - 1 structure encoder — processes structural context features
  - Fusion layer — concatenates all encoder outputs
  - Distributional head — outputs (mean, log_var) for each horizon step

Output: predicted distribution of log returns over the next N bars.
Training objective: Gaussian negative log-likelihood.

Usage:
    from model.world_model import WorldModel
    model = WorldModel(n_features=89, horizon=10, feature_groups=feature_groups)
    pred_mean, pred_log_var = model(state_tensor)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

import numpy as np


# ---------------------------------------------------------------------------
# Feature group specification
# ---------------------------------------------------------------------------

@dataclass
class FeatureGroup:
    """Describes a group of features for one encoder branch."""
    name: str
    indices: list[int]
    hidden_dim: int = 32

    @property
    def size(self) -> int:
        return len(self.indices)


@dataclass
class WorldModelConfig:
    """Configuration for the world model."""
    n_features: int = 89
    horizon: int = 10
    feature_groups: list[FeatureGroup] = field(default_factory=list)
    fusion_hidden: int = 64
    dropout: float = 0.1
    # Output head
    head_hidden: int = 32
    # Auxiliary: predict variance separately from mean
    share_var_encoder: bool = False
    # Depth: number of hidden layers per branch/fusion
    branch_depth: int = 2
    fusion_depth: int = 2
    # Legacy mode: use old shared-bottleneck DistributionHead (for old checkpoints)
    legacy: bool = False


# ---------------------------------------------------------------------------
# Per-branch encoder
# ---------------------------------------------------------------------------

class BranchEncoder(nn.Module):
    """
    MLP that encodes one feature group into a latent vector.

    Input:  (batch, input_dim)
    Output: (batch, hidden_dim)
    """

    def __init__(self, input_dim: int, hidden_dim: int, dropout: float = 0.1, depth: int = 2):
        super().__init__()
        layers = []
        layers.append(nn.Linear(input_dim, hidden_dim))
        layers.append(nn.LayerNorm(hidden_dim))
        layers.append(nn.GELU())
        layers.append(nn.Dropout(dropout))
        for _ in range(depth - 1):
            layers.append(nn.Linear(hidden_dim, hidden_dim))
            layers.append(nn.LayerNorm(hidden_dim))
            layers.append(nn.GELU())
            layers.append(nn.Dropout(dropout))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


# ---------------------------------------------------------------------------
# Distributional output head
# ---------------------------------------------------------------------------

class DistributionHead(nn.Module):
    """
    Maps fused representation to (mean, log_variance) for each horizon step.

    Uses SEPARATE pathways for mean and variance to avoid gradient imbalance.
    The variance pathway is deliberately deeper and wider to compensate for
    the historically weak gradient signal to the log_var head.
    """

    def __init__(self, input_dim: int, horizon: int, hidden_dim: int, dropout: float = 0.1, legacy: bool = False):
        super().__init__()
        self.horizon = horizon
        self.legacy = legacy

        if legacy:
            # Legacy architecture: single shared bottleneck (old checkpoints)
            self.shared = nn.Sequential(
                nn.Linear(input_dim, hidden_dim),
                nn.LayerNorm(hidden_dim),
                nn.GELU(),
                nn.Dropout(dropout),
            )
            self.mean_head = nn.Linear(hidden_dim, horizon)
            self.log_var_head = nn.Linear(hidden_dim, horizon)
            nn.init.zeros_(self.log_var_head.weight)
            nn.init.zeros_(self.log_var_head.bias)
        else:
            # New architecture: separate pathways to fix gradient starvation
            # --- Mean pathway ---
            self.mean_shared = nn.Sequential(
                nn.Linear(input_dim, hidden_dim),
                nn.LayerNorm(hidden_dim),
                nn.GELU(),
                nn.Dropout(dropout),
            )
            self.mean_head = nn.Linear(hidden_dim, horizon)

            # --- Variance pathway (deeper + wider) ---
            # Diagnostic showed mean/var gradient ratio = 400x.
            var_hidden = max(hidden_dim, 48)  # at least 48-dim for variance
            self.log_var_shared = nn.Sequential(
                nn.Linear(input_dim, var_hidden),
                nn.LayerNorm(var_hidden),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(var_hidden, var_hidden),
                nn.LayerNorm(var_hidden),
                nn.GELU(),
                nn.Dropout(dropout),
            )
            self.log_var_head = nn.Linear(var_hidden, horizon)
            nn.init.zeros_(self.log_var_head.weight)
            nn.init.zeros_(self.log_var_head.bias)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Returns
        -------
        mean : (batch, horizon)
        log_var : (batch, horizon) — log(variance), clamped for stability
        """
        if self.legacy:
            h = self.shared(x)
            mean = self.mean_head(h)
            log_var = self.log_var_head(h)
        else:
            # Mean pathway
            h_mean = self.mean_shared(x)
            mean = self.mean_head(h_mean)
            # Variance pathway (separate from mean to avoid gradient competition)
            h_var = self.log_var_shared(x)
            log_var = self.log_var_head(h_var)

        # Clamp for numerical stability
        log_var = torch.clamp(log_var, min=-20.0, max=5.0)
        return mean, log_var


# ---------------------------------------------------------------------------
# Main world model
# ---------------------------------------------------------------------------

class WorldModel(nn.Module):
    """
    Multi-branch distributional world model.

    Architecture:
        1. Each feature group → BranchEncoder → latent vector
        2. Concatenate all latent vectors → fusion MLP
        3. Fusion output → DistributionHead → (mean, log_var) per horizon

    Parameters
    ----------
    config : WorldModelConfig
        Model configuration.
    """

    def __init__(self, config: WorldModelConfig):
        super().__init__()
        self.config = config
        self.feature_groups = config.feature_groups
        self.horizon = config.horizon

        # Build branch encoders
        self.branches = nn.ModuleDict()
        for group in self.feature_groups:
            self.branches[group.name] = BranchEncoder(
                input_dim=group.size,
                hidden_dim=group.hidden_dim,
                dropout=config.dropout,
                depth=config.branch_depth,
            )

        # Total encoded dim = sum of all branch hidden dims
        total_encoded_dim = sum(g.hidden_dim for g in self.feature_groups)

        # Fusion layer (configurable depth)
        fusion_layers = []
        fusion_layers.append(nn.Linear(total_encoded_dim, config.fusion_hidden))
        fusion_layers.append(nn.LayerNorm(config.fusion_hidden))
        fusion_layers.append(nn.GELU())
        fusion_layers.append(nn.Dropout(config.dropout))
        for _ in range(config.fusion_depth - 1):
            fusion_layers.append(nn.Linear(config.fusion_hidden, config.fusion_hidden))
            fusion_layers.append(nn.LayerNorm(config.fusion_hidden))
            fusion_layers.append(nn.GELU())
            fusion_layers.append(nn.Dropout(config.dropout))
        self.fusion = nn.Sequential(*fusion_layers)

        # Distributional output head
        self.dist_head = DistributionHead(
            input_dim=config.fusion_hidden,
            horizon=config.horizon,
            hidden_dim=config.head_hidden,
            dropout=config.dropout,
            legacy=config.legacy,
        )

    def forward(
        self, x: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Forward pass.

        Parameters
        ----------
        x : torch.Tensor of shape (batch, n_features)

        Returns
        -------
        mean : torch.Tensor of shape (batch, horizon)
        log_var : torch.Tensor of shape (batch, horizon)
        """
        # Encode each feature group branch
        branch_outputs = []
        for group in self.feature_groups:
            branch_input = x[:, group.indices]
            branch_out = self.branches[group.name](branch_input)
            branch_outputs.append(branch_out)

        # Concatenate all branches
        fused = torch.cat(branch_outputs, dim=-1)

        # Fusion layers
        fused = self.fusion(fused)

        # Distributional head
        mean, log_var = self.dist_head(fused)

        return mean, log_var

    def predict_distribution(
        self, x: torch.Tensor
    ) -> dict[str, torch.Tensor]:
        """
        Full distributional prediction.

        Returns
        -------
        dict with keys: mean, variance, std, samples (if requested)
        """
        mean, log_var = self.forward(x)
        variance = torch.exp(log_var)
        std = torch.sqrt(variance)

        return {
            "mean": mean,
            "variance": variance,
            "std": std,
            "log_var": log_var,
        }

    def init_variance_from_data(
        self, targets: np.ndarray
    ) -> None:
        """
        Initialize the variance head bias from empirical target statistics.

        This is critical: the default initialization (log_var=0, i.e. var=1)
        is orders of magnitude off from the actual variance (~1e-6 for V75).
        Without this, the model spends most of its capacity learning the
        right variance scale rather than learning predictive features.

        Parameters
        ----------
        targets : np.ndarray of shape (n_samples, horizon)
        """
        targets_t = torch.tensor(targets, dtype=torch.float32)
        emp_var = targets_t.var(dim=0).clamp(min=1e-12)
        emp_log_var = torch.log(emp_var)

        # Set the bias of the log_var head to the empirical log variance
        with torch.no_grad():
            self.dist_head.log_var_head.bias.copy_(emp_log_var)
            # Initialize weights to small values near zero
            nn.init.normal_(self.dist_head.log_var_head.weight, std=0.01)

        print(f"Variance initialized: log_var range [{emp_log_var.min():.2f}, {emp_log_var.max():.2f}]")
        print(f"  std range: [{torch.sqrt(emp_var).min():.6f}, {torch.sqrt(emp_var).max():.6f}]")


# ---------------------------------------------------------------------------
# Loss function
# ---------------------------------------------------------------------------

def gaussian_nll_loss(
    pred_mean: torch.Tensor,
    pred_log_var: torch.Tensor,
    target: torch.Tensor,
    var_reg_weight: float = 0.01,
    target_log_var: torch.Tensor | None = None,
) -> torch.Tensor:
    """
    Gaussian negative log-likelihood loss with variance regularization.

    L = NLL + var_reg_weight * ||log_var - target_log_var||^2

    The variance regularization penalizes the model for predicting variance
    far from the empirical variance, preventing the common failure mode of
    overestimating uncertainty (which hurts NLL despite high coverage).

    Parameters
    ----------
    pred_mean : (batch, horizon)
    pred_log_var : (batch, horizon)
    target : (batch, horizon) -- actual log returns
    var_reg_weight : float -- weight for variance regularization (0 to disable)
    target_log_var : (horizon,) or None -- empirical log variance per step.
                     If None, computed from target tensor.

    Returns
    -------
    Scalar loss tensor
    """
    variance = torch.exp(pred_log_var)
    nll = 0.5 * (pred_log_var + (target - pred_mean) ** 2 / (variance + 1e-8))
    loss = nll.mean()

    # Variance regularization: penalize deviation from empirical variance
    if var_reg_weight > 0:
        if target_log_var is None:
            with torch.no_grad():
                emp_var = target.var(dim=0).clamp(min=1e-12)
                target_lv = torch.log(emp_var)
        else:
            target_lv = target_log_var.to(pred_log_var.device)

        var_reg = ((pred_log_var - target_lv) ** 2).mean()
        loss = loss + var_reg_weight * var_reg

    return loss


def quantile_huber_loss(
    pred_mean: torch.Tensor,
    target: torch.Tensor,
    quantiles: list[float] | None = None,
) -> torch.Tensor:
    """
    Quantile Huber loss as an auxiliary objective.

    Predicts multiple quantiles to capture the full distribution shape.
    """
    if quantiles is None:
        quantiles = [0.1, 0.25, 0.5, 0.75, 0.9]

    # pred_mean is (batch, horizon), we need to predict quantile values
    # For now, use the mean as a single quantile estimator
    # This is a simplified version — full quantile regression needs
    # output_dim = horizon * n_quantiles
    losses = []
    for q in quantiles:
        errors = target - pred_mean
        losses.append(
            torch.mean(
                torch.max(q * errors, (q - 1) * errors)
            )
        )

    return torch.stack(losses).mean()


# ---------------------------------------------------------------------------
# Build model from feature names
# ---------------------------------------------------------------------------

def build_model_from_features(
    feature_names: list[str],
    horizon: int = 10,
    dropout: float = 0.1,
    hidden_dim: int = 32,
    train_targets: np.ndarray | None = None,
    branch_depth: int = 2,
    fusion_depth: int = 2,
    legacy: bool = False,
) -> WorldModel:
    """
    Construct a WorldModel by auto-detecting feature groups from names.

    Groups features by their prefix (1min, 15min, 1h, etc.).
    If train_targets is provided, initializes variance head from empirical stats.
    If legacy=True, uses the old shared-bottleneck DistributionHead.
    """
    from collections import defaultdict

    # Group features by prefix
    groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for i, name in enumerate(sorted(feature_names)):
        # Extract prefix: "1min_close" → "1min", "n_bos" → "n", etc.
        parts = name.split("_")
        if parts[0] in ("1min", "15min", "1h"):
            prefix = parts[0]
        elif parts[0] in ("n", "nearest", "premium", "structure"):
            prefix = "structure"
        else:
            prefix = "other"
        groups[prefix].append((name, i))

    # Build FeatureGroup objects
    feature_groups = []
    for prefix in sorted(groups.keys()):
        items = groups[prefix]
        indices = [idx for _, idx in items]
        feature_groups.append(
            FeatureGroup(
                name=prefix,
                indices=indices,
                hidden_dim=hidden_dim,
            )
        )

    config = WorldModelConfig(
        n_features=len(feature_names),
        horizon=horizon,
        feature_groups=feature_groups,
        fusion_hidden=hidden_dim * 2,
        dropout=dropout,
        head_hidden=hidden_dim,
        branch_depth=branch_depth,
        fusion_depth=fusion_depth,
        legacy=legacy,
    )

    model = WorldModel(config)

    # Log architecture summary
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"WorldModel built: {total_params:,} params ({trainable_params:,} trainable)")
    for g in feature_groups:
        print(f"  {g.name}: {g.size} features -> {g.hidden_dim}d")

    # Initialize variance head from empirical target statistics
    if train_targets is not None:
        model.init_variance_from_data(train_targets)

    return model
