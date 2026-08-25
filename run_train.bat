@echo off
cd C:\Users\hp\Desktop\code\Genesis
echo Starting training at %date% %time% > train_log_v6.txt
python -m model.train_reduced --dataset data/dataset_R75_h10.npz --output-dir checkpoints_reduced --epochs 20 --hidden-dim 32 >> train_log_v6.txt 2>&1
echo Done at %date% %time% >> train_log_v6.txt
echo COMPLETE >> train_log_v6.txt
