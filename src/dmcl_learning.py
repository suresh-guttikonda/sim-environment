#!/usr/bin/env python3

# import os
# from DPF.dpf import DMCL
# import matplotlib.pyplot as plt
#
# curr_dir_path = os.path.dirname(os.path.abspath(__file__))
# config_file_path = os.path.join(curr_dir_path, 'config/turtlebot.yaml')
#
#
# dmcl = DMCL(config_file_path)
# dmcl.train()
#
# # to prevent plot from closing
# plt.ioff()
# plt.show()


import os
from DPF.dmcl import DMCL
import utils.helpers as helpers
from torch.utils.tensorboard import SummaryWriter
from pathlib import Path
import numpy as np

def train():
    curr_dir_path = os.path.dirname(os.path.abspath(__file__))
    config_filename = os.path.join(curr_dir_path, 'config/turtlebot.yaml')
    Path("saved_models").mkdir(parents=True, exist_ok=True)
    Path("best_models").mkdir(parents=True, exist_ok=True)

    dmcl = DMCL(config_filename)
    writer = SummaryWriter()

    num_epochs = 2000
    epoch_len = 50
    train_idx = 0
    eval_idx = 0
    acc = np.inf
    for curr_epoch in range(num_epochs):
        obs = dmcl.env.reset()
        gt_pose = helpers.get_gt_pose(dmcl.robot)
        particles, particles_probs = dmcl.init_particles(gt_pose)
        cum_loss = 0.
        cum_acc = 0.
        for curr_step in range(epoch_len):
            train_idx = train_idx + 1

            particles, loss, mse = dmcl.step(particles)

            writer.add_scalar('train/loss', loss.item(), train_idx)
            writer.add_scalar('train/mse', mse.item(), train_idx)
            cum_loss = cum_loss + loss.item()
            cum_acc = cum_acc + mse.item()

        mean_loss = cum_loss / epoch_len
        mean_acc = cum_acc / epoch_len
        print('mean loss: {0:.4f}, mean mse: {1:.4f}'.format(mean_loss, mean_acc))

        if curr_epoch%10 == 0:
            file_path = 'saved_models/train_model_{}.pt'.format(curr_epoch)
            dmcl.save(file_path)

            obs = dmcl.env.reset()
            gt_pose = helpers.get_gt_pose(dmcl.robot)
            particles, particles_probs = dmcl.init_particles(gt_pose)
            cum_acc = 0.
            for curr_step in range(epoch_len):
                eval_idx = eval_idx + 1

                particles, mse = dmcl.predict(particles)

                writer.add_scalar('eval/mse', mse.item(), eval_idx)
                cum_acc = cum_acc + mse.item()

            mean_acc = cum_acc / epoch_len
            if mean_acc < acc:
                acc = mean_acc
                file_path = 'best_models/model.pt'
                dmcl.save(file_path)
                print('=> best accuracy: {0:.4f}'.format(acc))

    writer.close()

def test():
    curr_dir_path = os.path.dirname(os.path.abspath(__file__))
    config_filename = os.path.join(curr_dir_path, 'config/turtlebot.yaml')
    dmcl = DMCL(config_filename, render=True)

    obs = dmcl.env.reset()
    gt_pose = helpers.get_gt_pose(dmcl.robot)
    particles, particles_probs = dmcl.init_particles(gt_pose)

if __name__ == '__main__':
    train()
    #test()
