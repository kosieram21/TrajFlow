import random
import time
import wandb
import torch
from datasets.Dataset import Dataset
from datasets.InD import InD
from datasets.EthUcy import EthUcy
from model.TrajFlow import TrajFlow, CausalEnocder, Flow
from train import train
from evaluate import evaluate
from visualize import visualize
from visualize_temp import visualize_temp

should_train = False
should_serialize = True
should_evaluate = False
should_visualize = True
simple_visualization = False
verbose = False
marginal = True

with wandb.init() as run:
	run.config.setdefaults({
		'seed': random.randint(0, 2**32 - 1),
		'encoder': 'CDE',
		'flow': 'CNF',
		'dataset': 'InD',
		'observation_site': 'zara2',
		'masked_data_ratio': 0
	})

	torch.manual_seed(run.config.seed)

	causal_encoder=CausalEnocder[run.config.encoder]
	flow=Flow[run.config.flow]
	dataset = Dataset[run.config.dataset]

	seq_len = 0
	input_dim = 0
	feature_dim = 0
	embedding_dim = 0
	hidden_dim = 0

	observation_site = None

	if dataset == Dataset.InD:
		seq_len = 100
		input_dim = 2
		feature_dim = 5
		embedding_dim = 128
		hidden_dim = 512
		training_epochs = 25
		evaulation_samples = 1000
		norm_rotate = False

		ind = InD(
			root="data",
			train_ratio=0.75, 
			train_batch_size=64, 
			test_batch_size=1,
			missing_rate=run.config.masked_data_ratio)
		observation_site = ind.observation_site1
	elif dataset == Dataset.EthUcy:
		seq_len = 12
		input_dim = 2
		feature_dim = 4
		embedding_dim = 128#32
		hidden_dim = 512#64
		training_epochs = 150
		evaulation_samples = 20
		norm_rotate = True

		ethucy = EthUcy(train_batch_size=128, test_batch_size=1, history=8, futures=12, smin=0.3, smax=1.7)
		observation_site = (
        	ethucy.eth_observation_site if run.config.observation_site == 'eth' else
        	ethucy.hotel_observation_site if run.config.observation_site == 'hotel' else
        	ethucy.univ_observation_site if run.config.observation_site == 'univ' else
			ethucy.zara1_observation_site if run.config.observation_site == 'zara1' else
			ethucy.zara2_observation_site if run.config.observation_site == 'zara2' else
        	ethucy.zara2_observation_site
    	)
	else:
		raise ValueError(f'{dataset.name} is not an experiment dataset')

	device = 'cuda' if torch.cuda.is_available() else 'cpu'

	traj_flow = TrajFlow(
		seq_len=seq_len, 
		input_dim=input_dim, 
		feature_dim=feature_dim, 
		embedding_dim=embedding_dim,
		hidden_dim=hidden_dim,
		causal_encoder=causal_encoder,
		flow=flow,
		marginal=marginal,
		norm_rotation=norm_rotate).to(device)
	
	num_parameters = sum(p.numel() for p in traj_flow.parameters() if p.requires_grad)
	if verbose:
		print(f'parameters: {num_parameters}')
	wandb.log({'parameters': num_parameters})

	train_start_time = time.time()

	total_loss = []
	if should_train:
		total_loss = train(
			observation_site=observation_site,
			model=traj_flow,
			epochs=training_epochs,
			lr=1e-3,
			weight_decay=0,
			gamma=0.999,
			verbose=verbose,
			device=device)
		
	train_end_time = time.time()
	train_runtime = train_end_time - train_start_time
	if verbose:
		print(train_runtime)
	wandb.log({'train runtime': train_runtime})

	traj_flow.eval()
	input, feature, _ = next(iter(observation_site.test_loader))
	input = input.to(device)
	feature = feature.to(device)
	inference_start_time = time.time()
	traj_flow.sample(input, feature, 100, 100)
	inference_end_time = time.time()
	inference_runtime = inference_end_time - inference_start_time
	if verbose:
		print(inference_runtime)
	wandb.log({'inference runtime': inference_runtime})
		
	for loss in total_loss:
		wandb.log({'loss': loss})
			
	if should_serialize:
		suffix = 'marginal' if marginal else 'joint'
		#model_name = f'trajflow_{suffix}.pt'
		model_name = f'trajflow_{suffix}_ind.pt'
		if should_train:
			torch.save(traj_flow.state_dict(), model_name)
		traj_flow.load_state_dict(torch.load(model_name))

	if should_evaluate:
		rmse, crps, min_ade, min_fde, nll = evaluate(
			observation_site=observation_site,
			model=traj_flow,
			num_samples=evaulation_samples,
			device=device)
		
		if verbose:
			print(f'rmse: {rmse}')
			print(f'crps: {crps}')
			print(f'min ade: {min_ade}')
			print(f'min fde: {min_fde}')
			print(f'nll: {nll}')
		wandb.log({'rmse': rmse, 'crps': crps, 'min ade': min_ade, 'min fde': min_fde, 'nll': nll})

	if should_visualize:
		visualize(
			observation_site=observation_site,
			model=traj_flow,
		 	num_samples=30,
		 	steps=1000,
		 	prob_threshold=0.001,
		 	output_dir='visualization',
		 	simple=simple_visualization,
		 	device=device)
		# visualize_temp(
		# 	data_loader=observation_site.test_loader,
		# 	model=traj_flow,
		# 	num_samples=20,
		# 	steps=1000,
		# 	prob_threshold=0.001,
		# 	output_dir='visualization_temp',
		# 	simple=simple_visualization,
		# 	device=device)
