@echo off
cd C:\Users\hp\Desktop\code\Genesis
python -m model.train_reduced --dataset data/dataset_R75_h10.npz --output-dir checkpoints_reduced --epochs 100 --hidden-dim 32 > train_log_v5.txt 2>&1
echo Training complete! Check train_log_v5.txt
