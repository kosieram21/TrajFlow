import time
import torch
import torch.nn.functional as F
import torch.distributions as dist
from datasets.EthUcy import EthUcy
from datasets.InD import InD
from model.FloMo import FloMo
from tqdm import tqdm

device = 'cuda' if torch.cuda.is_available() else 'cpu'
#torch.manual_seed(145841768)

ethucy = EthUcy(train_batch_size=128, test_batch_size=1, history=8, futures=12, smin=0.3, smax=1.7)
#observation_site = ethucy.eth_observation_site
#observation_site = ethucy.hotel_observation_site
#observation_site = ethucy.univ_observation_site
#observation_site = ethucy.zara1_observation_site
#observation_site = ethucy.zara2_observation_site
ind = InD(root="data", train_ratio=0.75, train_batch_size=64, test_batch_size=1, missing_rate=0)
observation_site = ind.observation_site1

#flomo = FloMo(hist_size=8, pred_steps=12, alpha=10, beta=0.2, gamma=0.02, num_in=2, num_feat=0, norm_rotation=True).to(device)
flomo = FloMo(hist_size=100, pred_steps=100, alpha=1, beta=0, gamma=0, num_in=2, num_feat=0, norm_rotation=True).to(device)
num_parameters = sum(p.numel() for p in flomo.parameters() if p.requires_grad)
print(f'parameters: {num_parameters}')

flomo.train()

optim = torch.optim.Adam(flomo.parameters(), lr=1e-3, weight_decay=0)
#scheduler = torch.optim.lr_scheduler.ExponentialLR(optim, gamma=0.999)

train_start_time = time.time()

for epoch in range(25):
    losses = []
    for input, _, target in (pbar := tqdm(observation_site.train_loader)):
        input = input.to(device)
        target = target.to(device)

        input = torch.FloatTensor(observation_site.denormalize(input.cpu().numpy())).to(device)
        target = torch.FloatTensor(observation_site.denormalize(target.cpu().numpy())).to(device)

        full_trajectory = torch.cat([input, target], dim=1)
        trajectory_mean = full_trajectory.mean(dim=1, keepdim=True)

        input = input - trajectory_mean
        target = target - trajectory_mean

        log_prob = flomo.log_prob(target, input)
        loss = -torch.mean(log_prob)
            
        optim.zero_grad()
        loss.backward()
        optim.step()
        #scheduler.step()
            
        losses.append(loss)

        pbar.set_description(f'Epoch {epoch} Loss {loss.item():.4f}')

    losses = torch.stack(losses)
    pbar.set_description(f'Epoch {epoch} Loss {torch.mean(losses):.4f}')

train_end_time = time.time()
train_runtime = train_end_time - train_start_time
print(f'train runtime: {train_runtime}')

def rmse(y_true, y_pred):
    mse = F.mse_loss(y_true.expand_as(y_pred), y_pred, reduction="mean")
    rmse = torch.sqrt(mse)
    return rmse

def crps(y_true, y_pred):
    num_samples = y_pred.shape[0]
    absolute_error = torch.mean(torch.abs(y_pred - y_true), dim=0)

    if num_samples == 1:
        return  torch.mean(absolute_error)

    y_pred, _ = torch.sort(y_pred, dim=0)
    empirical_cdf = torch.arange(num_samples, device=y_pred.device).view(-1, 1, 1) / num_samples
    b0 = torch.mean(y_pred, dim=0)
    b1 = torch.mean(y_pred * empirical_cdf, dim=0)

    crps = absolute_error + b0 - 2 * b1
    crps = torch.mean(crps)
    return crps

def min_ade(y_true, y_pred):
    distances = torch.norm(y_pred - y_true.expand_as(y_pred), dim=-1)
    min_distances = torch.min(distances, dim=0).values
    return torch.mean(min_distances)

def min_fde(y_true, y_pred):
    fde = torch.norm(y_pred[:,-1,:] - y_true[:,-1,:], dim=-1)
    return fde.min()

flomo.eval()

input, feature, _ = next(iter(observation_site.test_loader))
input = input.to(device)
feature = feature.to(device)
inference_start_time = time.time()
flomo.sample(100, input)
inference_end_time = time.time()
inference_runtime = inference_end_time - inference_start_time
print(f'inference runtime: {inference_runtime}')

with torch.no_grad():
    nll_sum = 0
    rmse_sum = 0
    crps_sum = 0
    min_ade_sum = 0
    min_fde_sum = 0
    count = 0

    for test_input, test_feature, test_target in observation_site.test_loader:
        test_input = test_input.to(device)
        test_feature = test_feature.to(device)
        test_target = test_target.to(device)

        test_input = torch.FloatTensor(observation_site.denormalize(test_input.cpu().numpy())).to(device)
        test_target = torch.FloatTensor(observation_site.denormalize(test_target.cpu().numpy())).to(device)

        full_trajectory = torch.cat([test_input, test_target], dim=1)
        trajectory_mean = full_trajectory.mean(dim=1, keepdim=True)

        test_input = test_input - trajectory_mean
        test_target = test_target - trajectory_mean

        # NLL same as training loss
        #log_prob = flomo.log_prob(test_target, test_input)
        #nll_sum += -torch.mean(log_prob)

        # sample based evaluation
        samples, _ = flomo.sample(1000, test_input)
        samples = samples[:,:test_target.shape[1],:]
        #test_target = torch.tensor(observation_site.denormalize(test_target.cpu().numpy())).to(device)
        #samples = torch.tensor(observation_site.denormalize(samples.cpu().numpy())).to(device)

        rmse_sum += rmse(test_target, samples)
        crps_sum += crps(test_target, samples)
        min_ade_sum += min_ade(test_target, samples)
        min_fde_sum += min_fde(test_target, samples)
        count += 1

    print(f'rmse: {rmse_sum / count}')
    print(f'crps: {crps_sum / count}')
    print(f'min ade: {min_ade_sum / count}')
    print(f'min fde: {min_fde_sum / count}')
    #print(f'nll: {nll_sum / count}')