import wandb
from collections import defaultdict

api = wandb.Api()


#sweep_id = '8bg3cief' # GRU-DNF
#sweep_id = 'e1cbol5u' # GRU-CNF
#sweep_id = 'ojbledwd' # CDE-DNF
#sweep_id = 'mb3crbk9' # CDE-CNF
sweep_id = '3haj7vsf' # CDE-CNF (joint)
sweep = api.sweep(f'mitchkos21-university-of-minnesota/trajflow_dry_run/{sweep_id}')

config_counts = defaultdict(int)
nll_results = defaultdict(int)
crps_results = defaultdict(int)
rmse_results = defaultdict(int)
#min_ade_results = defaultdict(int)
#min_fde_results = defaultdict(int)
min_ade_results = defaultdict(lambda: float('inf'))
min_fde_results = defaultdict(lambda: float('inf'))
train_runtime_results = defaultdict(int)
inference_runtime_results = defaultdict(int)
parameters_results = defaultdict(int)

for run in sweep.runs:
    config = run.config
    key = config['observation_site']

    summary = run.summary
    if 'nll' in summary:
        nll_result = summary['nll']
        crps_result = summary['crps']
        rmse_result = summary['rmse']
        min_ade_result = summary['min ade']
        min_fde_result = summary['min fde']
        train_runtime_result = summary['train runtime']
        inference_runtime_result = summary['inference runtime']
        parameters_result = summary['parameters']

        config_counts[key] += 1
        nll_results[key] += nll_result
        crps_results[key] += crps_result
        rmse_results[key] += rmse_result
        min_ade_results[key] = min(min_ade_results[key], min_ade_result)#+= min_ade_result
        min_fde_results[key] = min(min_fde_results[key], min_fde_result)#+= min_fde_result
        train_runtime_results[key] += train_runtime_result
        inference_runtime_results[key] += inference_runtime_result
        parameters_results[key] += parameters_result

average_train_runtime = 0
average_inference_runtime = 0
total_sites = 0

for key in config_counts.keys():
    num_runs = config_counts[key]
    nll = nll_results[key] = nll_results[key] / num_runs
    crps = crps_results[key] = crps_results[key] / num_runs
    rmse = rmse_results[key] =  rmse_results[key] / num_runs
    min_ade = min_ade_results[key] #= min_ade_results[key] / num_runs
    min_fde = min_fde_results[key] #= min_fde_results[key] / num_runs
    train_runtime = train_runtime_results[key] = train_runtime_results[key] / num_runs
    inference_runtime = inference_runtime_results[key] = inference_runtime_results[key] / num_runs
    parameters = parameters_results[key] = parameters_results[key] / num_runs

    average_train_runtime += train_runtime
    average_inference_runtime += inference_runtime
    total_sites += 1

    print(key)
    print(f'min ade: {min_ade}')
    print(f'min fde: {min_fde}')
    # print(f'nll: {nll}')
    # print(f'crps: {crps}')
    # print(f'rmse: {rmse}')
    print(f'train runtime: {train_runtime}')
    print(f'inference runtime: {inference_runtime}')
    print(f'parameters: {parameters}')
    print('')

print(f'average train runtime: {average_train_runtime / total_sites}')
print(f'average inference runtime: {average_inference_runtime / total_sites}')