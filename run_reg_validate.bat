@echo off
cd C:\Users\hp\Desktop\code\Genesis
echo === Original (drop=0.1, wd=1e-4, ep=20) === > reg_val_results.txt
python -m eval.validate_reduced --dataset data/dataset_R75_h10.npz --checkpoint checkpoints_reduced/world_model_best.pt --output-dir checkpoints_reduced/validation --train-days 60 --val-days 30 --step-days 30 2>&1 >> reg_val_results.txt

echo === Variant A (drop=0.2, wd=1e-3, ep=20) === >> reg_val_results.txt
python -m eval.validate_reduced --dataset data/dataset_R75_h10.npz --checkpoint checkpoints_reg_A/world_model_best.pt --output-dir checkpoints_reg_A/validation --train-days 60 --val-days 30 --step-days 30 2>&1 >> reg_val_results.txt

echo === Variant B (drop=0.15, wd=5e-4, ep=15) === >> reg_val_results.txt
python -m eval.validate_reduced --dataset data/dataset_R75_h10.npz --checkpoint checkpoints_reg_B/world_model_best.pt --output-dir checkpoints_reg_B/validation --train-days 60 --val-days 30 --step-days 30 2>&1 >> reg_val_results.txt

echo COMPLETE >> reg_val_results.txt
