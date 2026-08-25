@echo off
python -m model.sweep --data data/dataset_R75_h10.npz --save-dir checkpoints/sweep --epochs 50
echo SWEEP_DONE
