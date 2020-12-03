#!/usr/bin/env python3

import numpy as np
import torch
from utils import helpers, constants, datautils
import networks.networks as nets
from torchvision import models, transforms
from torch.utils.data import DataLoader
from torch import nn, optim
from torch.utils.tensorboard import SummaryWriter

class Measurement(object):
    """
    """

    def __init__(self, vision_model_name='resnet34', loss='mse'):

        if vision_model_name == 'resnet34':
            self.vision_model_name = vision_model_name
            self.vision_model = models.resnet34(pretrained=True).to(constants.DEVICE)
            for param in self.vision_model.parameters():
                param.requires_grad = False
            layers = ['layer4', 'avgpool']

        self.feature_extractor = nets.FeatureExtractor(self.vision_model, layers).to(constants.DEVICE)
        self.likelihood_net = nets.LikelihoodNetwork().to(constants.DEVICE)

        params = list(self.likelihood_net.parameters())
        self.optimizer = optim.Adam(params, lr=2e-4)

        if loss == 'mse':
            self.loss_fn_name = loss
            self.loss_fn = nn.MSELoss()

        self.writer = SummaryWriter()
        self.train_idx = 0
        self.eval_idx = 0

        self.best_eval_accuracy = np.inf

        self.num_data_files = 75

    def get_obs_data_loader(self, file_idx):
        obs_file_name = 'sup_data/rnd_pose_obs_data/data_{:04d}.pkl'.format(file_idx)
        particles_file_name = 'sup_data/rnd_particles_data/particles_{:04d}.pkl'.format(file_idx)

        # reference https://pytorch.org/docs/stable/torchvision/models.html
        composed = transforms.Compose([
                    datautils.Rescale(256),
                    datautils.RandomCrop(224),
                    datautils.ToTensor(),
                    datautils.Normalize()])

        obs_dataset = datautils.ObservationDataset(obs_pkl_file=obs_file_name,
                                    particles_pkl_file=particles_file_name,
                                    transform=composed)

        obs_data_loader = DataLoader(obs_dataset,
                            batch_size = constants.BATCH_SIZE,
                            shuffle = True,
                            num_workers = 0)

        return obs_data_loader

    def set_train_mode(self):
        self.vision_model.train()
        self.feature_extractor.train()

    def set_eval_mode(self):
        self.vision_model.eval()
        self.feature_extractor.eval()

    def train(self, train_epochs=1, eval_epochs=1):
        eval_epoch = 25
        save_epoch = 50

        # iterate per epoch
        for epoch in range(train_epochs):
            # TRAIN
            self.set_train_mode()
            training_loss = []

            # iterate per pickle data file
            for file_idx in range(self.num_data_files):
                obs_data_loader = self.get_obs_data_loader(file_idx)

                # iterate per batch
                batch_loss = 0
                for _, batch_samples in enumerate(obs_data_loader):
                    self.optimizer.zero_grad()

                    # get data
                    batch_rgbs = batch_samples['state']['rgb'].to(constants.DEVICE)
                    batch_gt_poses = batch_samples['pose'].to(constants.DEVICE)
                    batch_gt_particles = batch_samples['gt_particles'].to(constants.DEVICE)
                    batch_gt_labels = batch_samples['gt_labels'].to(constants.DEVICE).squeeze()
                    batch_est_particles = batch_samples['est_particles'].to(constants.DEVICE)
                    batch_est_labels = batch_samples['est_labels'].to(constants.DEVICE).squeeze()

                    # transform particles from orientation angle to cosine and sine values
                    trans_batch_gt_particles = helpers.transform_poses(batch_gt_particles)
                    trans_batch_est_particles = helpers.transform_poses(batch_est_particles)

                    # get encoded image features
                    features = self.feature_extractor(batch_rgbs)

                    # approach [p, img + 4]
                    img_features = features['avgpool'].view(constants.BATCH_SIZE, 1, -1)
                    repeat_img_features = img_features.repeat(1, batch_gt_particles.shape[1], 1)

                    input_est_features = torch.cat([trans_batch_est_particles, repeat_img_features], axis=-1).squeeze()
                    input_gt_features = torch.cat([trans_batch_gt_particles, repeat_img_features], axis=-1).squeeze()

                    gt_embeddings, gt_likelihoods = self.likelihood_net(input_gt_features)
                    est_embeddings, est_likelihoods = self.likelihood_net(input_est_features)

                    if self.loss_fn_name == 'mse':
                        likelihoods = torch.cat([gt_likelihoods, est_likelihoods], dim=0).squeeze()
                        labels = torch.cat([batch_gt_labels, batch_est_labels], dim=0)
                        loss = self.loss_fn(likelihoods, labels)

                    loss.backward()
                    self.optimizer.step()

                    batch_loss = batch_loss + float(loss)

                # log
                self.writer.add_scalar('training/{}_b_loss'.format(self.loss_fn_name), batch_loss, self.train_idx)
                self.train_idx = self.train_idx + 1
                training_loss.append(batch_loss)

            #
            print('mean loss: {0:4f}'.format(np.mean(training_loss)))

            if epoch%save_epoch == 0:
                file_name = 'saved_models/' + 'likelihood_{0}_idx_{1}.pth'.format(self.loss_fn_name, epoch)
                self.save(file_name)

            if epoch%eval_epoch == 0:
                self.eval(num_epochs=eval_epochs)

        print('training done')
        self.writer.close()

    def eval(self, num_epochs=1):

        total_loss = 0
        # iterate per epoch
        for epoch in range(num_epochs):
            # EVAL
            self.set_eval_mode()
            evaluation_loss = []

            # iterate per pickle data file
            for file_idx in range(self.num_data_files):
                obs_data_loader = self.get_obs_data_loader(file_idx)

                # iterate per batch
                batch_loss = 0
                with torch.no_grad():
                    for _, batch_samples in enumerate(obs_data_loader):
                        # get data
                        batch_rgbs = batch_samples['state']['rgb'].to(constants.DEVICE)
                        batch_gt_poses = batch_samples['pose'].to(constants.DEVICE)
                        batch_gt_particles = batch_samples['gt_particles'].to(constants.DEVICE)
                        batch_gt_labels = batch_samples['gt_labels'].to(constants.DEVICE).squeeze()
                        batch_est_particles = batch_samples['est_particles'].to(constants.DEVICE)
                        batch_est_labels = batch_samples['est_labels'].to(constants.DEVICE).squeeze()

                        # transform particles from orientation angle to cosine and sine values
                        trans_batch_gt_particles = helpers.transform_poses(batch_gt_particles)
                        trans_batch_est_particles = helpers.transform_poses(batch_est_particles)

                        # get encoded image features
                        features = self.feature_extractor(batch_rgbs)

                        # approach [p, img + 4]
                        img_features = features['avgpool'].view(constants.BATCH_SIZE, 1, -1)
                        repeat_img_features = img_features.repeat(1, batch_gt_particles.shape[1], 1)

                        input_est_features = torch.cat([trans_batch_est_particles, repeat_img_features], axis=-1).squeeze()
                        input_gt_features = torch.cat([trans_batch_gt_particles, repeat_img_features], axis=-1).squeeze()

                        gt_embeddings, gt_likelihoods = self.likelihood_net(input_gt_features)
                        est_embeddings, est_likelihoods = self.likelihood_net(input_est_features)

                        if self.loss_fn_name == 'mse':
                            likelihoods = torch.cat([gt_likelihoods, est_likelihoods], dim=0).squeeze()
                            labels = torch.cat([batch_gt_labels, batch_est_labels], dim=0)
                            loss = self.loss_fn(likelihoods, labels)

                        batch_loss = batch_loss + float(loss)

                    # log
                    self.writer.add_scalar('training/{}_b_loss'.format(self.loss_fn_name), batch_loss, self.train_idx)
                    self.eval_idx = self.eval_idx + 1
                    evaluation_loss.append(batch_loss)

            total_loss = total_loss + np.mean(evaluation_loss)

        if total_loss < self.best_eval_accuracy:
            self.best_eval_accuracy = total_loss
            print('new best loss: {0:4f}'.format(total_loss))

            file_name = 'best_models/' + 'likelihood_{0}_best.pth'.format(self.loss_fn_name)
            self.save(file_name)

    def test(self, file_name):
        self.load(file_name)
        self.set_eval_mode()

    def save(self, file_name):
        torch.save({
            'likelihood_net': self.likelihood_net.state_dict(),
            'optimizer': self.optimizer.state_dict()
        }, file_name)
        # print('=> created checkpoint')

    def load(self, file_name):
        checkpoint = torch.load(file_name)
        self.likelihood_net.load_state_dict(checkpoint['likelihood_net'])
        self.optimizer.load_state_dict(checkpoint['optimizer'])
        # print('=> loaded checkpoint')

if __name__ == '__main__':
    measurement = Measurement()
    train_epochs=1
    eval_epochs=1
    measurement.train(train_epochs, eval_epochs)
    file_name = 'best_models/likelihood_mse_best.pth'
    measurement.test(file_name)
