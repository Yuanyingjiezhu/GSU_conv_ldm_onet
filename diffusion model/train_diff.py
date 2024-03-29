import torch
import torch.optim as optim
from tensorboardX import SummaryWriter
import numpy as np
import os
import argparse
import time, datetime
import matplotlib; matplotlib.use('Agg')
from src import config, data
from src.checkpoints import CheckpointIO
from collections import defaultdict
from torch.utils.data import TensorDataset, DataLoader
import shutil

from src.encoder.diffusion import DiffusionModel
from src.encoder.diffusion_arch import DiffusionNet
from src.encoder.diff_trainer import Trainer
from torch.utils.data import Dataset


# Arguments
parser = argparse.ArgumentParser(
    description='Train a 3D reconstruction model.'
)
parser.add_argument('config', type=str, help='Path to config file.')
parser.add_argument('--no-cuda', action='store_true', help='Do not use cuda.')
parser.add_argument('--exit-after', type=int, default=-1,
                    help='Checkpoint and exit after specified number of seconds'
                         'with exit code 2.')

args = parser.parse_args()
cfg = config.load_config(args.config, 'configs/default.yaml')
is_cuda = (torch.cuda.is_available() and not args.no_cuda)
device = torch.device("cuda" if is_cuda else "cpu")
# Set t0
t0 = time.time()

# Shorthands
out_dir = cfg['training']['out_dir']
batch_size = cfg['training']['batch_size']
backup_every = cfg['training']['backup_every']
exit_after = args.exit_after

model_selection_metric = cfg['training']['model_selection_metric']
if cfg['training']['model_selection_mode'] == 'maximize':
    model_selection_sign = 1
elif cfg['training']['model_selection_mode'] == 'minimize':
    model_selection_sign = -1
else:
    raise ValueError('model_selection_mode must be '
                     'either maximize or minimize.')

# Output directory
if not os.path.exists(out_dir):
    os.makedirs(out_dir)

shutil.copyfile(args.config, os.path.join(out_dir, 'config.yaml'))

# Dataset
class MyCustomDataset(Dataset):
    def __init__(self, filename):
        self.data = torch.load(filename)
    
    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        tensor = self.data[index]
        tensor = tensor.squeeze(0)
        return tensor


inputs = MyCustomDataset(filename='/usr/prakt/s0136/con_test/dataset/final_std_1_latent_train_1_new.pt')


train_dataset = inputs
val_dataset = inputs

""" train_loader = torch.utils.data.DataLoader(
    train_dataset, batch_size=batch_size, num_workers=cfg['training']['n_workers'], shuffle=True,
    collate_fn=data.collate_remove_none,
    worker_init_fn=data.worker_init_fn) """

train_loader = torch.utils.data.DataLoader(
    train_dataset, batch_size=cfg['training']['batch_size'], shuffle=True,
    collate_fn=data.collate_remove_none,
    worker_init_fn=data.worker_init_fn)

""" val_loader = torch.utils.data.DataLoader(
        val_dataset, batch_size=1, num_workers=cfg['training']['n_workers_val'], shuffle=False,
    collate_fn=data.collate_remove_none,
    worker_init_fn=data.worker_init_fn) """

val_loader = torch.utils.data.DataLoader(
        val_dataset, batch_size=1, shuffle=False,
    collate_fn=data.collate_remove_none,
    worker_init_fn=data.worker_init_fn)


# Model
model = DiffusionModel(model=DiffusionNet(**cfg["diffusion_model_specs"]), **cfg["diffusion_specs"]) 
model.to(device)


# Intialize training
optimizer = optim.Adam(model.parameters(), lr=cfg["diff_lr"])
# optimizer = optim.SGD(model.parameters(), lr=1e-4, momentum=0.9)

# Trainer
time_step = cfg['diffusion_specs']['timesteps']
trainer = Trainer(model, optimizer, batch_size)

checkpoint_io = CheckpointIO(out_dir, model=model, optimizer=optimizer)
try:
    load_dict = checkpoint_io.load('model.pt')
except FileExistsError:
    load_dict = dict()
epoch_it = load_dict.get('epoch_it', 0)
it = load_dict.get('it', 0)
metric_val_best = load_dict.get(
    'loss_val_best', -model_selection_sign * np.inf)

if metric_val_best == np.inf or metric_val_best == -np.inf:
    metric_val_best = -model_selection_sign * np.inf
print('Current best validation metric (%s): %.8f'
      % (model_selection_metric, metric_val_best))
logger = SummaryWriter(os.path.join(out_dir, 'logs'))

# Shorthands
print_every = cfg['training']['print_every']
checkpoint_every = cfg['training']['checkpoint_every']
validate_every = cfg['training']['validate_every']

# Print model
nparameters = sum(p.numel() for p in model.parameters())
print('Total number of parameters: %d' % nparameters)

print('output path: ', cfg['training']['out_dir'])


while True:
    epoch_it += 1

    for i, data in enumerate(train_loader, 0):
        #print(data)
        it += 1
        loss = trainer.train_step(data)
        logger.add_scalar('train/loss', loss, it)

        # Print output
        if print_every > 0 and (it % print_every) == 0:
            t = datetime.datetime.now()
            print('[Epoch %02d] it=%03d, loss=%.4f, time: %.2fs, %02d:%02d'
                     % (epoch_it, it, loss, time.time() - t0, t.hour, t.minute))


        # Save checkpoint
        if (checkpoint_every > 0 and (it % checkpoint_every) == 0):
            print('Saving checkpoint')
            checkpoint_io.save('model.pt', epoch_it=epoch_it, it=it,
                               loss_val_best=metric_val_best)

        # Backup if necessary
        if (backup_every > 0 and (it % backup_every) == 0):
            print('Backup checkpoint')
            checkpoint_io.save('model_%d.pt' % it, epoch_it=epoch_it, it=it,
                               loss_val_best=metric_val_best)
        # Run validation
#        if validate_every > 0 and (it % validate_every) == 0:
#            eval_dict = trainer.evaluate(val_loader)
#            metric_val = eval_dict[model_selection_metric]
#             print('Validation metric (%s): %.4f'
#                   % (model_selection_metric, metric_val))
# 
#             for k, v in eval_dict.items():
#                 logger.add_scalar('val/%s' % k, v, it)

#             if model_selection_sign * (metric_val - metric_val_best) > 0:
#                 metric_val_best = metric_val
#                 print('New best model (loss %.4f)' % metric_val_best)
#                 checkpoint_io.save('model_best.pt', epoch_it=epoch_it, it=it,
#                                    loss_val_best=metric_val_best)

        # Exit if necessary
        if exit_after > 0 and (time.time() - t0) >= exit_after:
            print('Time limit reached. Exiting.')
            checkpoint_io.save('model.pt', epoch_it=epoch_it, it=it,
                               loss_val_best=metric_val_best)
            exit(3)
