@echo off
cd C:\Users\hp\Desktop\code\Genesis
echo Starting structure precomputation at %date% %time% > precompute_log.txt
python -m features.precompute --input data/ticks_R75.parquet --output data/structures_R75.parquet --chunk-size 5000 >> precompute_log.txt 2>&1
echo Done at %date% %time% >> precompute_log.txt
echo COMPLETE >> precompute_log.txt
