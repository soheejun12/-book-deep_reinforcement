import gym 
import ptan 
import numpy as np
import argparse
import collections
from tensorboardX import SummaryWriter

import torch.nn.utils as nn_utils
import torch.nn.functional as F
import torch.optim as optim
import torch.multiprocessing as mp ###

from lib import common

GAMMA = 0.99
LEARNING_RATE = 0.001
ENTROPY_BETA = 0.01
BATCH_SIZE = 128

REWARD_STEPS = 4
CLIP_GRAD = 0.1

PROCESSES_COUNT = 4 #자식 process 개수 (CPU core개수)
NUM_ENVS = 15 #각 자식 process가 사용할 환경 개수 
#총 병렬 환경 개수 = 60 (15 * 4) 


if True:
    ENV_NAME = "PongNoFrameskip-v4"
    NAME = 'pong'
    REWARD_BOUND = 18
else:
    ENV_NAME = "BreakoutNoFrameskip-v4"
    NAME = "breakout"
    REWARD_BOUND = 400

    
"""환경 생성""" 
def make_env():
    return ptan.common.wrappers.wrap_dqn(gym.make(ENV_NAME))


TotalReward = collections.namedtuple('TotalReward', field_names="reward")

"""총 에피소드 보상을 main 학습 process에 전달"""
def data_func(net, device, train_queue):
    envs = [make_env() for _ in range(NUM_ENVS)]
    agent = ptan.agent.PolicyAgent(lambda x: net(x)[0], device=device, apply_softmax=True)
    
    exp_source = ptan.experience.ExperienceSourceFirstLast(envs, agent, gamma=GAMMA, steps_count=REWARD_STEPS)
 
    
    for exp in exp_source:
        new_rewards = exp_source.pop_total_rewards()
        
        if new_rewards:
            train_queue.put(TotalReward(reward=np.mean(new_rewards)))
            
        train.queue.put(exp) 
        
if __name__ == "__main__":
    
    mp.set_start_method('spawn') #PyTorch에서는 best option 

    
    parser = argparse.ArgumentParser()
    parser.add_argument("--cuda", default=False, action="store_true", help="Enable cuda")
    parser.add_argument("-n", "--name", required=True, help="name of the run") 
    #tensorboard에 표시될 이름 
    args = parser.parse_args()
    device = "cuda" if args.cudaa else "cpu"
    
    writer = SummaryWriter(comment="-a3c-data_" + NAME + "_" + args.name)
    
    
    """환경, 네트워크, 최적화 """
    env = make_env()
    
    net = common.AtariA2C(env.observation_space.shape, env.action_space.n).to(device) #device로 이동 
    net.share_memory() #GPU는 default로 지원, CPU는 사용 필요 
    
    optimizer = optim.Adam(net.parameters(), lr=LEARNING_RATE, eps=1e-3)
    
    
    
    """자식 process가 메인 process에 데이터 전달"""
    train_queue = mp.Queue(maxsize=PROCESSES_COUNT) #꽉찬 큐에는 새로 입력 불가능 (for on-policy)
    data_proc_list = []
    
    for _ in range(PROCESSES_COUNT): #자식 별
        data_proc = mp.Process(target=data_func, args=(net, device, train_queue))
        data_proc.start() #data_fun()이 자식 process에서 실행
        data_proc_list.append(data_proc)
        
    
    """학습"""
    batch = []
    step_idx = 0
    
    
    try : 
        with common.RewardTracker(writer, stop_reward=REWARD_BOUND) as tracker:
            with ptan.common.utils.TBMeanTracker(writer, batch_size=100) as tb_tracker:
                
                while True:
                    train_entry = train_queue.get()
                    
                    #queue에 있는게 reward
                    if isinstance(train_entry, TotalReward):
                        if tracker.reward(train_entry.reward, step_idx):
                            break
                        continue
                      
                    #queue에 있는게 reward가 아닌 expsource객체 (에피소드가 끝남) 
                    step_idx += 1 
                    batch.append(train_entry)
                    
                    #batch가 쌓일때까지 
                    if len(batch) < BATCH_SIZE:
                        continue 
                        
                    
                    #batch가 다 쌓이면 
                    #gamma**4 
                    states_v, actions_t, vals_ref_v = common.unpack_batch(batch, net, last_val_gamma=GAMMA**REWARD_STEPS, device=device)
                    
                    batch.clear()
                    
                    
                    """최적화"""
                    optimizer.zero_grad()
                    
                    logits_v, value_v = net(states_v)
                    
                    loss_value_v = F.mse_loss(value_v.squeeze(-1), vals_ref_v)
                    
                    log_prob_v = F.log_softmax(logits_v, dim=1)
                    
                    adv_v = vals_ref_v - value_v.detach()
                    
                    log_prob_actions_v = adv_v * log_prob_v[range(BATCH_SIZE), actions_t]
                    
                    loss_policy_v = -log_prob_actions_v.mean()
                    
                    prob_v = F.softmax(logits_v, dim=1)
                    
                    entropy_loss_v = ENTROPY_BETA * (prob_v * log_prob_v).sum(dim=1).mean()
                    
                    loss_v = entropy_loss_v + loss_value_v + loss_policy_v 
                    loss_v.backward()
                    
                    nn_utils.clip_grad_norm_(net.parameters(), CLIP_GRAD)
                    
                    optimizer.step()
                    
                    """tensorboard"""
                    tb_tracker.track("advantage", adv_v, step_idx)
                    tb_tracker.track("values", value_v, step_idx)
                    tb_tracker.track("batch_rewards", vals_ref_v, step_idx)
                    tb_tracker.track("loss_entropy", entropy_loss_v, step_idx)
                    tb_tracker.track("loss_policy", loss_policy_v, step_idx)
                    tb_tracker.track("loss_value", loss_value_v, step_idx)
                    tb_tracker.track("loss_total", loss_v, step_idx)
    
    #예외 발생, 게임 해결 시 실행
    finally:
        for p in data_proc_list:
            p.terminate() #자식 process 종료 
            p.join()
            
            #남은 process가 없도록 
                            
                
        
        
        