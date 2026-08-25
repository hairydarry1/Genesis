@echo off
cd C:\Users\hp\Desktop\code\Genesis

echo === Variant A: dropout=0.2, wd=1e-3 === > reg_sweep_log.txt
python -m model.train_reduced --dataset data/dataset_R75_h10.npz --output-dir checkpoints_reg_A --epochs 20 --hidden-dim 32 >> reg_sweep_log.txt 2>&1

echo === Variant B: dropout=0.15, wd=5e-4 === >> reg_sweep_log.txt
python -m model.train_reduced --dataset data/dataset_R75_h10.npz --output-dir checkpoints_reg_B --epochs 15 --hidden-dim 32 >> reg_sweep_log.txt 2>&1

echo COMPLETE >> reg_sweep_log.txt
