#!/usr/bin/env python3

import cv2
import numpy as np
import pybullet as p
import tensorflow as tf
from stable_baselines3 import PPO
from stable_baselines3.ppo import MlpPolicy

def normalize(angle):
    """
    Normalize the angle to [-pi, pi]
    :param float angle: input angle to be normalized
    :return float: normalized angle
    """
    quaternion = p.getQuaternionFromEuler(np.array([0, 0, angle]))
    euler = p.getEulerFromQuaternion(quaternion)
    return euler[2]

def calc_odometry(old_pose, new_pose):
    """
    Calculate the odometry between two poses
    :param ndarray old_pose: pose1 (x, y, theta)
    :param ndarray new_pose: pose2 (x, y, theta)
    :return ndarray: odometry (odom_x, odom_y, odom_th)
    """
    x1, y1, th1 = old_pose
    x2, y2, th2 = new_pose

    abs_x = (x2 - x1)
    abs_y = (y2 - y1)

    th1 = normalize(th1)
    sin = np.sin(th1)
    cos = np.cos(th1)

    th2 = normalize(th2)
    odom_th = normalize(th2 - th1)
    odom_x = cos * abs_x + sin * abs_y
    odom_y = cos * abs_y - sin * abs_x

    odometry = np.array([odom_x, odom_y, odom_th])
    return odometry

def sample_motion_odometry(old_pose, odometry):
    """
    Sample new pose based on give pose and odometry
    :param ndarray old_pose: given pose (x, y, theta)
    :param ndarray odometry: given odometry (odom_x, odom_y, odom_th)
    :return ndarray: new pose (x, y, theta)
    """
    x1, y1, th1 = old_pose
    odom_x, odom_y, odom_th = odometry

    th1 = normalize(th1)
    sin = np.sin(th1)
    cos = np.cos(th1)

    x2 = x1 + (cos * odom_x - sin * odom_y)
    y2 = y1 + (sin * odom_x + cos * odom_y)
    th2 = normalize(th1 + odom_th)

    new_pose = np.array([x2, y2, th2])
    return new_pose

def decode_image(img, resize=None):
    """
    Decode image
    :param img_str: image encoded as a png in a string
    :param resize: tuple of width, height, new size of image (optional)
    :return np.ndarray: image (k, H, W, 1)
    """
    #TODO
    # img = cv2.imdecode(img, -1)
    if resize is not None:
        img = cv2.resize(img, resize)
    return img

def process_floor_map(floormap):
    """
    Decode floormap
    :param floormap: floor map image as ndarray (H, W)
    :return np.ndarray: image (H, W, 1)
    """
    floormap = np.atleast_3d(decode_image(floormap))

    # # floor map image need to be transposed and inverted here
    # floormap = 255 - np.transpose(floormap, axes=[1, 0, 2])

    # floor map image need to be inverted here
    floormap = 255 - floormap

    floormap = normalize_map(floormap.astype(np.float32))
    return floormap

def normalize_map(x):
    """
    Normalize map input
    :param x: map input (H, W, ch)
    :return np.ndarray: normalized map (H, W, ch)
    """
    # rescale to [0, 2], later zero padding will produce equivalent obstacle
    return x * (2.0/255.0)

def normalize_observation(x):
    """
    Normalize observation input: an rgb image or a depth image
    :param x: observation input (56, 56, ch)
    :return np.ndarray: normalized observation (56, 56, ch)
    """
    # resale to [-1, 1]
    if x.ndim == 2 or x.shape[2] == 1:  # depth
        return x * (2.0 / 100.0) - 1.0
    else:   # rgb
        return x * (2.0 / 255.0) - 1.0

def process_raw_image(image):
    """
    Decode and normalize image
    :param image: image encoded as a png (H, W, ch)
    :return np.ndarray: images (56, 56, ch) normalized for training
    """

    image = decode_image(image, (56, 56))
    image = normalize_observation(np.atleast_3d(image.astype(np.float32)))

    return image

def get_discrete_action():
    """
    Get manual keyboard action
    :return int: discrete action for moving forward/backward/left/right
    """
    key = input('Enter Key: ')
    # default stay still
    action = 4
    if key == 'w':
        action = 0  # forward
    elif key == 's':
        action = 1  # backward
    elif key == 'd':
        action = 2  # right
    elif key == 'a':
        action = 3  # left
    return action

def load_action_model(env, device, path):
    """
    Initialize pretrained action sampler model
    """

    model = PPO(MlpPolicy, env, verbose=1, device=device)
    model = PPO.load(path)

    return model

def gather_episode_stats(env, params, action_model):
    """
    Run the gym environment and collect the required stats
    :param params: parsed parameters
    :param action_model: pretrained action sampler model
    :return dict: episode stats data containing:
        odometry, true poses, observation, particles, particles weights, floor map
    """

    agent = params.agent
    trajlen = params.trajlen
    num_particles = params.num_particles
    particles_cov = params.init_particles_cov
    particles_distr = params.init_particles_distr

    odometry = []
    true_poses = []
    observation = []

    obs = env.reset()   # already processed
    observation.append(obs)

    global_map = env.get_floor_map()    # already processed

    old_pose = env.get_robot_state()['pose']
    true_poses.append(old_pose)

    for _ in range(trajlen-1):
        if agent == 'manual':
            action = get_discrete_action()
        elif agent == 'pretrained':
            action, _ = action_model.predict(obs)
        else:
            # default random action
            action = env.action_space.sample()

        # take action and get new observation
        obs, reward, done, _ = env.step(action)
        observation.append(obs)

        # get new robot state after taking action
        new_pose = env.get_robot_state()['pose']
        true_poses.append(new_pose)

        # calculate actual odometry b/w old pose and new pose
        odom = calc_odometry(old_pose, new_pose)
        odometry.append(odom)
        old_pose = new_pose

    # end of episode
    odom = calc_odometry(old_pose, new_pose)
    odometry.append(odom)

    # sample random particles and corresponding weights
    if particles_distr == 'uniform':
        init_particles = env.get_random_particles(num_particles)
    elif particles_distr == 'gaussian':
        init_particles = env.get_random_particles(num_particles, true_poses[0], particles_cov)
    init_particles_weights = np.full((num_particles, ), (1./num_particles))

    episode_data = {}
    episode_data['global_map'] = global_map # (height, width, 1)
    episode_data['odometry'] = np.stack(odometry)  # (trajlen, 3)
    episode_data['init_particles'] = init_particles   # (num_particles, 3)
    episode_data['true_states'] = np.stack(true_poses)  # (trajlen, 3)
    episode_data['observation'] = np.stack(observation) # (trajlen, height, width, 3)
    episode_data['init_particles_weights'] = init_particles_weights   # (num_particles,)

    return episode_data

def get_batch_data(env, params, action_model):
    """
    Gather batch of episode stats
    :param params: parsed parameters
    :param action_model: pretrained action sampler model
    :return dict: episode stats data containing:
        odometry, true poses, observation, particles, particles weights, floor map
    """

    trajlen = params.trajlen
    batch_size = params.batch_size
    map_size = params.global_map_size
    num_particles = params.num_particles

    odometry = []
    global_map = []
    observation = []
    true_states = []
    init_particles = []
    init_particles_weights = []

    for _ in range(batch_size):
        episode_data = gather_episode_stats(env, params, action_model)

        odometry.append(episode_data['odometry'])
        global_map.append(episode_data['global_map'])
        true_states.append(episode_data['true_states'])
        observation.append(episode_data['observation'])
        init_particles.append(episode_data['init_particles'])
        init_particles_weights.append(episode_data['init_particles_weights'])

    batch_data = {}
    batch_data['odometry'] = np.stack(odometry)
    batch_data['global_map'] = np.stack(global_map)
    batch_data['true_states'] = np.stack(true_states)
    batch_data['observation'] = np.stack(observation)
    batch_data['init_particles'] = np.stack(init_particles)
    batch_data['init_particles_weights'] = np.stack(init_particles_weights)

    # sanity check
    assert list(batch_data['odometry'].shape) == [batch_size, trajlen, 3]
    assert list(batch_data['true_states'].shape) == [batch_size, trajlen, 3]
    assert list(batch_data['observation'].shape) == [batch_size, trajlen, 56, 56, 3]
    assert list(batch_data['init_particles'].shape) == [batch_size, num_particles, 3]
    assert list(batch_data['init_particles_weights'].shape) == [batch_size, num_particles]
    assert list(batch_data['global_map'].shape) == [batch_size, map_size[0], map_size[1], map_size[2]]

    return batch_data

def serialize_tf_record(episode_data):
    states = episode_data['true_states']
    odometry = episode_data['odometry']
    global_map = episode_data['global_map']
    observation = episode_data['observation']
    init_particles = episode_data['init_particles']
    init_particles_weights = episode_data['init_particles_weights']

    record = {
        'state': tf.train.Feature(float_list=tf.train.FloatList(value=states.flatten())),
        'state_shape': tf.train.Feature(int64_list=tf.train.Int64List(value=states.shape)),
        'odometry': tf.train.Feature(float_list=tf.train.FloatList(value=odometry.flatten())),
        'odometry_shape': tf.train.Feature(int64_list=tf.train.Int64List(value=odometry.shape)),
        'global_map': tf.train.Feature(float_list=tf.train.FloatList(value=global_map.flatten())),
        'global_map_shape': tf.train.Feature(int64_list=tf.train.Int64List(value=global_map.shape)),
        'observation': tf.train.Feature(float_list=tf.train.FloatList(value=observation.flatten())),
        'observation_shape': tf.train.Feature(int64_list=tf.train.Int64List(value=observation.shape)),
        'init_particles': tf.train.Feature(float_list=tf.train.FloatList(value=init_particles.flatten())),
        'init_particles_shape': tf.train.Feature(int64_list=tf.train.Int64List(value=init_particles.shape)),
        'init_particles_weights': tf.train.Feature(float_list=tf.train.FloatList(value=init_particles_weights.flatten())),
        'init_particles_weights_shape': tf.train.Feature(int64_list=tf.train.Int64List(value=init_particles_weights.shape)),
    }

    return tf.train.Example(features=tf.train.Features(feature=record)).SerializeToString()

def deserialize_tf_record(record):
    tfrecord_format = {
        'state': tf.io.FixedLenSequenceFeature((), dtype=tf.float32, allow_missing=True),
        'state_shape': tf.io.FixedLenSequenceFeature((), dtype=tf.int64, allow_missing=True),
        'odometry': tf.io.FixedLenSequenceFeature((), dtype=tf.float32, allow_missing=True),
        'odometry_shape': tf.io.FixedLenSequenceFeature((), dtype=tf.int64, allow_missing=True),
        'global_map': tf.io.FixedLenSequenceFeature((), dtype=tf.float32, allow_missing=True),
        'global_map_shape': tf.io.FixedLenSequenceFeature((), dtype=tf.int64, allow_missing=True),
        'observation': tf.io.FixedLenSequenceFeature((), dtype=tf.float32, allow_missing=True),
        'observation_shape': tf.io.FixedLenSequenceFeature((), dtype=tf.int64, allow_missing=True),
        'init_particles': tf.io.FixedLenSequenceFeature((), dtype=tf.float32, allow_missing=True),
        'init_particles_shape': tf.io.FixedLenSequenceFeature((), dtype=tf.int64, allow_missing=True),
        'init_particles_weights': tf.io.FixedLenSequenceFeature((), dtype=tf.float32, allow_missing=True),
        'init_particles_weights_shape': tf.io.FixedLenSequenceFeature((), dtype=tf.int64, allow_missing=True),
    }

    features_tensor = tf.io.parse_single_example(record, tfrecord_format)
    return features_tensor
