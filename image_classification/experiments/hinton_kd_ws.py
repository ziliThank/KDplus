import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="torch.nn.functional")
from comet_ml import Experiment
from fastai.vision import *
import torch
import argparse
import os
from image_classification.arguments import get_args
from image_classification.datasets.dataset import get_dataset
from image_classification.utils.utils import *
from image_classification.models.custom_resnet import *
from kd_trainer import KDTrainer
from kd.quartizer import *
import random

args = get_args(description='Hinton KD', mode='train')
expt = 'hinton-kd'
print(args)
random.seed(args.seed)  # random and transforms
torch.backends.cudnn.deterministic=True  # cudnn
torch.manual_seed(args.seed)

if args.gpu != 'cpu':
    args.gpu = int(args.gpu)
    torch.cuda.set_device(args.gpu)
    torch.cuda.manual_seed(args.seed)

hyper_params = {
    "dataset": args.dataset,
    "model": args.model,
    "num_classes": 10,
    "batch_size": 64,
    "num_epochs": args.epoch,
    "learning_rate": 1e-4,
    "momentum": 0.9,
    "seed": args.seed,
    "percentage":args.percentage,
    "gpu": args.gpu,
    "temperature" : 20,
    "alpha" : 0.2,
    "weight_decay": 5e-4,
    "stage":0,
    "p_prune": args.prune_percentage,
    "bits": args.bits_weight_sharing  
}

data = get_dataset(dataset=hyper_params['dataset'],
                   batch_size=hyper_params['batch_size'],
                   percentage=args.percentage)


learn, net = get_model(hyper_params['model'], hyper_params['dataset'], data, teach=True)
learn.model, net = learn.model.to(args.gpu), net.to(args.gpu)

teacher = learn.model

sf_student = None
sf_teacher = None

if args.api_key:
    project_name = expt + '-' + hyper_params['model'] + '-' + hyper_params['dataset']
    experiment = Experiment(api_key=args.api_key, project_name=project_name, workspace=args.workspace)
    experiment.log_parameters(hyper_params)

savename = get_savename(hyper_params, experiment=expt)
optimizer = torch.optim.SGD(net.parameters(), lr=hyper_params["learning_rate"], momentum=hyper_params["momentum"], weight_decay=hyper_params["weight_decay"])

loss_function = nn.KLDivLoss(reduction='mean')
loss_function2 = nn.CrossEntropyLoss()
best_val_loss = 100
best_val_acc = 0
# refactor it to a trainer 
trainer = KDTrainer(net,
                    teacher,
                    data,
                    sf_teacher,
                    sf_student,
                    loss_function,
                    loss_function2,
                    optimizer=optimizer,
                    hyper_params=hyper_params,
                    epoch=hyper_params['num_epochs'],
                    savename=savename,
                    best_val_acc=best_val_acc,
                    expt=expt)
net, train_loss, val_loss, val_acc, best_val_acc = trainer.train(gpu=args.gpu)

if args.api_key:
    experiment.log_metric("train_loss", train_loss)
    experiment.log_metric("val_loss", val_loss)
    experiment.log_metric("val_acc", val_acc * 100)

# ======= Below are customized KD & DC code ==========
# reload the best model
net.load_state_dict(torch.load(savename))
net.eval()
val_loss, val_acc = trainer.eval_model(model=net, quartized=False)
print(f"original net_0, val_loss: {val_loss}, val_acc: {val_acc} ")

## weight sharing by yujie
apply_weight_sharing(net, bits=args.bits_weight_sharing)
val_loss, val_acc = trainer.eval_model(model=net, quartized=False)
print(f"net_1 after weight sharing, val_loss: {val_loss}, val_acc: {val_acc}")

loss_function = nn.CrossEntropyLoss()
best_val_acc = 0  
new_trainer = KDTrainer(net,
                        teacher=None,
                        data=data,
                        sf_teacher=None,
                        sf_student=None,
                        loss_function=loss_function,
                        loss_function2=None,
                        optimizer=optimizer,
                        hyper_params=hyper_params,
                        epoch=hyper_params['num_epochs'],
                        savename=savename,
                        best_val_acc=best_val_acc,
                        bits_weight_sharing=args.bits_weight_sharing) 

net, train_loss, val_loss, val_acc, best_val_acc = new_trainer.train(gpu=args.gpu)

net.load_state_dict(torch.load(savename))
net.eval()
val_loss, val_acc = trainer.eval_model(model=net, quartized=False)
print(f"net_2 after retraining net_1, val_loss: {val_loss}, val_acc: {val_acc} ")

