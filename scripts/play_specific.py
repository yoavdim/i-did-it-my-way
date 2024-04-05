#!/usr/bin/env python3
import isaacgym

assert isaacgym
import torch
import numpy as np

import glob
import pickle as pkl
import os.path

from go1_gym.envs import *
from go1_gym.envs.base.legged_robot_config import Cfg
from go1_gym.envs.go1.go1_config import config_go1
from go1_gym.envs.go1.velocity_tracking import VelocityTrackingEasyEnv

from tqdm import tqdm

from scripts.config_env import config_env

RUNS_FOLDER_NAME = "runs*"  # "runs" or "runs_azure"

def load_policy_it(logdir, it):
    import os
    if it >= 0:
        body = torch.jit.load(logdir + f'/checkpoints/body_{it:06d}.jit')
        adaptation_module = torch.jit.load(logdir + f'/checkpoints/adaptation_module_{it:06d}.jit')
    else:
        body = torch.jit.load(logdir + '/checkpoints/body_latest.jit')
        adaptation_module = torch.jit.load(logdir + '/checkpoints/adaptation_module_latest.jit')

    def policy(obs, info={}):
        i = 0
        latent = adaptation_module.forward(obs["obs_history"].to('cpu'))
        action = body.forward(torch.cat((obs["obs_history"].to('cpu'), latent), dim=-1))
        info['latent'] = latent
        return action

    return policy


def load_env_it(label, time, it, headless=False):
    if os.path.isdir("./.git"):
        dirs = glob.glob(f"./{RUNS_FOLDER_NAME}/{label}/{time}")
    else:
        dirs = glob.glob(f"../{RUNS_FOLDER_NAME}/{label}/{time}")
    print("earliest: %s" % sorted(dirs)[0])
    print("selected: %s" % sorted(dirs)[-1])
    
    logdir = sorted(dirs)[-1]# [0]
    with open(logdir + "/parameters.pkl", 'rb') as file:
        pkl_cfg = pkl.load(file)
        print(pkl_cfg.keys())
        cfg = pkl_cfg["Cfg"]
        print(cfg.keys())

        for key, value in cfg.items():
            if hasattr(Cfg, key):
                for key2, value2 in cfg[key].items():
                    setattr(getattr(Cfg, key), key2, value2)

    # turn off DR for evaluation script
    Cfg.domain_rand.push_robots = False
    Cfg.domain_rand.randomize_friction = False
    Cfg.domain_rand.randomize_gravity = False
    Cfg.domain_rand.randomize_restitution = False
    Cfg.domain_rand.randomize_motor_offset = False
    Cfg.domain_rand.randomize_motor_strength = False
    Cfg.domain_rand.randomize_friction_indep = False
    Cfg.domain_rand.randomize_ground_friction = False
    Cfg.domain_rand.randomize_base_mass = False
    Cfg.domain_rand.randomize_Kd_factor = False
    Cfg.domain_rand.randomize_Kp_factor = False
    Cfg.domain_rand.randomize_joint_friction = False
    Cfg.domain_rand.randomize_com_displacement = False

    Cfg.env.num_recording_envs = 1
    Cfg.env.num_envs = 1
    Cfg.terrain.num_rows = 5
    Cfg.terrain.num_cols = 5
    Cfg.terrain.border_size = 0
    Cfg.terrain.center_robots = True
    Cfg.terrain.center_span = 1
    Cfg.terrain.teleport_robots = True

    Cfg.domain_rand.lag_timesteps = 6
    Cfg.domain_rand.randomize_lag_timesteps = True
    Cfg.control.control_type = "actuator_net"

    # our part
    #config_env(Cfg)  todo wrong since using config_many, also reading from pkl anyway

    from go1_gym.envs.wrappers.history_wrapper import HistoryWrapper

    env = VelocityTrackingEasyEnv(sim_device='cuda:0', headless=False, cfg=Cfg)
    env = HistoryWrapper(env)

    # load policy
    policy = load_policy_it(logdir, it)

    return env, policy


def play_go1_it(it: int = -1, date: str = "2*", time: str = "*", headless=True):
    """
    dates can be:
        "2*" for newest,
        "pretrain-v0" for original,
        "2023-09-01" for a specific one

    times are in the format:
        "*" for newest,
        "085728.400006" for specific

    for the latest iteration, just pass a negative iteration number
    """
    from ml_logger import logger

    from pathlib import Path
    from go1_gym import MINI_GYM_ROOT_DIR
    import glob
    import os

    label = f"gait-conditioned-agility/{date}/train*"  # both train&train_many

    env, policy = load_env_it(label, time, it, headless=headless)

    num_eval_steps = 250
    gaits = {"pronking": [0, 0, 0],
             "trotting": [0.5, 0, 0],
             "bounding": [0, 0.5, 0],
             "pacing": [0, 0, 0.5]}

    x_vel_cmd, y_vel_cmd, yaw_vel_cmd = 1.5, 0.0, 0.0
    body_height_cmd = 0.0
    step_frequency_cmd = 3.0
    gait = torch.tensor(gaits["trotting"])
    footswing_height_cmd = 0.08
    pitch_cmd = 0.0
    roll_cmd = 0.0
    stance_width_cmd = 0.25

    measured_x_vels = np.zeros(num_eval_steps)
    target_x_vels = np.ones(num_eval_steps) * x_vel_cmd
    joint_positions = np.zeros((num_eval_steps, 12))

    obs = env.reset()

    for i in tqdm(range(num_eval_steps)):
        with torch.no_grad():
            actions = policy(obs)
        env.commands[:, 0] = x_vel_cmd
        env.commands[:, 1] = y_vel_cmd
        env.commands[:, 2] = yaw_vel_cmd
        env.commands[:, 3] = body_height_cmd
        env.commands[:, 4] = step_frequency_cmd
        env.commands[:, 5:8] = gait
        env.commands[:, 8] = 0.5
        env.commands[:, 9] = footswing_height_cmd
        env.commands[:, 10] = pitch_cmd
        env.commands[:, 11] = roll_cmd
        env.commands[:, 12] = stance_width_cmd
        obs, rew, done, info = env.step(actions)

        measured_x_vels[i] = env.base_lin_vel[0, 0]
        joint_positions[i] = env.dof_pos[0, :].cpu()

    # plot target and measured forward velocity
    from matplotlib import pyplot as plt
    import matplotlib
    matplotlib.use('TKAgg')
    fig, axs = plt.subplots(2, 1, figsize=(12, 5))
    axs[0].plot(np.linspace(0, num_eval_steps * env.dt, num_eval_steps), measured_x_vels, color='black', linestyle="-", label="Measured")
    axs[0].plot(np.linspace(0, num_eval_steps * env.dt, num_eval_steps), target_x_vels, color='black', linestyle="--", label="Desired")
    axs[0].legend()
    axs[0].set_title("Forward Linear Velocity")
    axs[0].set_xlabel("Time (s)")
    axs[0].set_ylabel("Velocity (m/s)")

    axs[1].plot(np.linspace(0, num_eval_steps * env.dt, num_eval_steps), joint_positions[:,:6], linestyle="-", label=[str(i) for i in range(6)])
    axs[1].set_title("Joint Positions")
    axs[1].set_xlabel("Time (s)")
    axs[1].set_ylabel("Joint Position (rad)")
    plt.legend()

    plt.tight_layout()
    plt.show()


if __name__ == '__main__':
    print("insert date/runtime and then the iteration number (leave empty for newest):")
    dateNtime = input()
    dateNtime = dateNtime.split("/") if dateNtime else ("2*", "*")
    date = dateNtime[0]
    time = dateNtime[-1]  # allows paths as: 2024-03-03/train/171750.264034
    iteration = input()
    iteration = int(iteration) if len(iteration) else -1
    # to see the environment rendering, set headless=False
    play_go1_it(date=date, time=time, it=iteration, headless=False)