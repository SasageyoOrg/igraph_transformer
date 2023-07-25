"""
# ---------------------------------------------------------------------------- #
#                                IMPORTING LIBS                                #
# ---------------------------------------------------------------------------- #
"""


import numpy as np
import os
import time
import random
import glob
import argparse, json
import statistics
import matplotlib.pyplot as plt

import torch

import torch.optim as optim
from torch.utils.data import DataLoader
from torch.utils.data import SubsetRandomSampler
from sklearn.model_selection import StratifiedKFold
from train.train_IGs_node_classification import train_epoch, evaluate_network 

class DotDict(dict):
    def __init__(self, **kwds):
        self.update(kwds)
        self.__dict__ = self


# --------------------- IMPORTING CUSTOM MODULES/METHODS --------------------- #
from nets.load_net import gnn_model 
from data.data import LoadData

'''
# ---------------------------------------------------------------------------- #
#                               UTILTY FUNCTIONS                               #
# ---------------------------------------------------------------------------- #
'''
# --------------------------------- GPU Setup -------------------------------- #
def gpu_setup(use_gpu, gpu_id):
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)  

    if torch.cuda.is_available() and use_gpu:
        print('cuda available with GPU:',torch.cuda.get_device_name(0))
        device = torch.device("cuda")
    else:
        print('cuda not available')
        device = torch.device("cpu")
    return device


# ---------------------- VIEWING MODEL CONFIG AND PARAMS --------------------- #
def view_model_param(MODEL_NAME, net_params):
    model = gnn_model(MODEL_NAME, net_params)
    total_param = 0
    print("MODEL DETAILS:\n")
    #print(model)
    for param in model.parameters():
        # print(param.data.size())
        total_param += np.prod(list(param.data.size()))
    print('MODEL/Total parameters:', MODEL_NAME, total_param)
    return total_param

'''
# ---------------------------------------------------------------------------- #
#                                 TRAINING CODE                                #
# ---------------------------------------------------------------------------- #
'''
def train_test_pipeline(MODEL_NAME, dataset, params, net_params, dirs, classes):
    start0 = time.time()
    per_epoch_time = []
    DATASET_NAME = dataset.name
    # Model Configuration -- edit config profile
    if net_params['self_loop']:
        st = time.time()
        print("\n[!] Adding graph self-loops")
        dataset._add_self_loops()
        print('Time Self Loops:',time.time()-st)
        
            
    if net_params['lap_pos_enc']:
        st = time.time()
        print("\n[!] Adding Laplacian positional encoding.")
        dataset._add_laplacian_positional_encodings(net_params['pos_enc_dim'])
        print('Time LapPE:',time.time()-st)
        
    if net_params['wl_pos_enc']:
        st = time.time()
        print("\n[!] Adding WL positional encoding.")
        dataset._add_wl_positional_encodings()
        print('Time WL PE:',time.time()-st)
    
    if net_params['full_graph']:
        st = time.time()
        print("\n[!] Converting the given graphs to full graphs..")
        dataset._make_full_graph()
        print('Time taken to convert to full graphs:',time.time()-st)

    trainset = dataset.train
    # Assuming datasetDGL.train[:][1] is the list of tensors
    tensor_list = trainset[:][1]

    # Extract the numbers from the tensors using list comprehension
    labels_list = [tensor.item() for tensor in tensor_list]
        
    root_log_dir, root_ckpt_dir, root_plots_dir,write_file_name, write_config_file = dirs
    device = net_params['device']
    
    # Write network and optimization hyper-parameters in folder config/
    with open(write_config_file + '.txt', 'w') as f:
        f.write("""Dataset: {},\nModel: {}\n\nparams={}\n\nnet_params={}\n\n\nTotal Parameters: {}\n\n"""                .format(DATASET_NAME, MODEL_NAME, params, net_params, net_params['total_param']))
        
    log_dir = os.path.join(root_log_dir, "RUN_" + str(0))
    
    # setting seeds
    random.seed(params['seed'])
    np.random.seed(params['seed'])
    torch.manual_seed(params['seed'])
    if device.type == 'cuda':
        torch.cuda.manual_seed(params['seed'])
    
    print("Number of Classes: ", net_params['n_classes'], "\n")


    model = gnn_model(MODEL_NAME, net_params)
    
    model = model.to(device)

    optimizer = optim.Adam(model.parameters(), lr=params['init_lr'], weight_decay=params['weight_decay'])
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min',
                                                     factor=params['lr_reduce_factor'],
                                                     patience=params['lr_schedule_patience'],
                                                     verbose=True)
    
    

    
    # for fold evaluation
    train_accs, train_f1s, test_accs, test_f1s = [], [], [], []
        
    skf = StratifiedKFold(n_splits=params['kfold_splits'], shuffle=True, random_state=params['seed'])
    # At any point you can hit Ctrl + C to break out of training early.
    for fold, (train_idx, test_idx) in enumerate(skf.split(trainset, labels_list)):
      
        epoch_train_losses , epoch_train_accs, epoch_train_f1s = [], [], []
        epoch_test_losses , epoch_test_accs, epoch_test_f1s = [], [], []
        
        # Create data loaders for this fold
        train_subsampler = SubsetRandomSampler(train_idx)
        test_subsampler = SubsetRandomSampler(test_idx)

        train_loader = DataLoader(trainset, 
                                  batch_size=params['batch_size'],
                                  collate_fn=dataset.collate, 
                                  sampler=train_subsampler)
        test_loader = DataLoader(trainset, 
                                batch_size=params['batch_size'], 
                                collate_fn=dataset.collate, 
                                sampler=test_subsampler)
        print("\n\n-------------------------------------------------------------- ")
        print(f"\nTraining Fold {fold + 1}/{params['kfold_splits']}")
        
        # ... (same steps as in the original train pipeline, but applied to each fold)
        model = gnn_model(MODEL_NAME, net_params)
        model = model.to(device)
        optimizer = optim.Adam(model.parameters(), lr=params['init_lr'], weight_decay=params['weight_decay'])
        scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=params['lr_reduce_factor'],
                                                         patience=params['lr_schedule_patience'], verbose=True)
        try:
            for epoch in range(params['epochs']):
                start = time.time()

                epoch_train_loss, epoch_train_acc, epoch_train_f1, optimizer, t = train_epoch(model, optimizer, device, train_loader, epoch)
                # epoch_train_loss, epoch_train_acc, epoch_train_f1 = evaluate_network(model, device, train_loader, epoch)
                epoch_test_loss, epoch_test_acc, epoch_test_f1 = evaluate_network(model, device, test_loader, epoch)


                epoch_train_losses.append(epoch_train_loss)
                epoch_train_accs.append(epoch_train_acc)
                epoch_train_f1s.append(epoch_train_f1)
                
                epoch_test_losses.append(epoch_test_loss)
                epoch_test_accs.append(epoch_test_acc)
                epoch_test_f1s.append(epoch_test_f1)

                per_epoch_time.append(time.time()-start)
                expected_time_seconds = statistics.mean(per_epoch_time) * (params['epochs'] - epoch) * (params['kfold_splits']-fold)
                expected_hours = int(expected_time_seconds // 3600)
                expected_minutes = int((expected_time_seconds % 3600) // 60)
                
                print(f"  train time: {per_epoch_time[-1]:.4f}| "
                      f"expected time to end: {expected_hours:02d}:{expected_minutes:02d} h. | "
                      f"lr: {optimizer.param_groups[0]['lr']}| "
                      
                      f"train_loss: {epoch_train_loss:.4f}| "
                      f"test_loss: {epoch_test_loss:.4f}| "
                      f"train_acc: {epoch_train_acc:.4f}| "
                      f"test_acc: {epoch_test_acc:.4f}| "
                      f"train_f1: {epoch_train_f1:.4f}| "
                      f"test_f1: {epoch_test_f1:.4f}|\n")
                
                
                # torch.save(model.state_dict(), '{}.pkl'.format(ckpt_dir + "/epoch_" + str(epoch)))

                # files = glob.glob(ckpt_dir + '/*.pkl')
                # for file in files:
                #     epoch_nb = file.split('_')[-1]
                #     epoch_nb = int(epoch_nb.split('.')[0])
                #     if epoch_nb < epoch-1:
                #         os.remove(file)

                scheduler.step(epoch_test_loss)

                if optimizer.param_groups[0]['lr'] < params['min_lr']:
                    print("\n!! LR SMALLER OR EQUAL TO MIN LR THRESHOLD.")
                    break
                    
                # Stop training after params['max_time'] hours
                if time.time()-start0 > params['max_time']*3600:
                    print('-' * 89)
                    print("Max_time for training elapsed {:.2f} hours, so stopping".format(params['max_time']))
                    break
  
        except KeyboardInterrupt:
            print('-' * 89)
            print('Exiting from training early because of KeyboardInterrupt')
        
        # Saving checkpoint
        ckpt_dir = os.path.join(root_ckpt_dir, "RUN_")
        if not os.path.exists(ckpt_dir):
            os.makedirs(ckpt_dir)
        torch.save({
          'epoch': epoch,
          'model_state_dict': model.state_dict(),
          'optimizer_state_dict': optimizer.state_dict(),
          'loss': epoch_train_loss,
        }, ckpt_dir + "/fold_" + str(fold+1))
        
        
        # Plot loss curve for the fold
        plots_dir = os.path.join(root_plots_dir, f"fold_{str(fold+1)}")
        if not os.path.exists(plots_dir):
            os.makedirs(plots_dir)
        plt.plot(range(epoch+1), epoch_train_losses, label=f"Fold {fold+1} Train Loss")
        plt.plot(range(epoch+1), epoch_test_losses, label=f"Fold {fold+1} Test Loss")
        plt.xlabel("Epoch")
        plt.ylabel("Loss")
        plt.title(f"Loss Curve - Fold {fold+1}")
        plt.legend()
        plt.savefig(os.path.join(plots_dir, f"fold_{fold+1}_loss_curve.png"))
        plt.close() 
        
        
        # After each fold, we evaluate the model on the test set
        train_loss, train_acc, train_f1 = evaluate_network(model, device, train_loader, epoch)
        test_loss, test_acc, test_f1 = evaluate_network(model, device, test_loader, epoch)

        # # Plot the ROC curve
        # plt.plot(fpr, tpr, label=f'ROC Curve (AUC = {roc_auc:.2f})')
        # plt.plot([0, 1], [0, 1], 'k--')  # Plot the diagonal line
        # plt.xlabel('False Positive Rate')
        # plt.ylabel('True Positive Rate')
        # plt.title('Receiver Operating Characteristic (ROC) Curve')
        # plt.legend()
        # plt.savefig(os.path.join(plots_dir, f"fold_{fold+1}_ROC_curve.png"))
        

        
        # Plot the confusion matrix
        # plt.imshow(confusion_mat, cmap='Blues')
        # plt.title(f"Confusion Matrix - Fold {fold+1}")
        # plt.colorbar()
        # plt.xlabel('Predicted Label')
        # plt.ylabel('True Label')
        # plt.xticks(np.arange(len(classes)), classes, rotation=45)
        # plt.yticks(np.arange(len(classes)), classes)
        # plt.savefig(os.path.join(plots_dir, f"fold_{fold+1}_CM_.png"))
        # plt.close() 

        train_accs.append(train_acc)
        train_f1s.append(train_f1)
        test_accs.append(test_acc)
        test_f1s.append(test_f1)

        print(f"\nFold {fold + 1}/{params['kfold_splits']}")
        print(f"Train Loss: {train_loss:.4f} | Test Loss: {test_loss:.4f}")
        print(f"Train Accuracy: {train_acc:.4f} | Test Accuracy: {test_acc:.4f}")
        print(f"Train F1 Score: {train_f1:.4f} | Test F1 Score: {test_f1:.4f}")
        
    # ... (calculating average performance metrics and saving model as in the original train pipeline)
    avg_train_acc = np.mean(train_accs)
    avg_test_acc = np.mean(test_accs)

    std_train_acc = np.std(train_accs)
    std_test_acc = np.std(test_accs)
    
    avg_train_f1 = np.mean(train_f1s)
    avg_test_f1 = np.mean(test_f1s)

    std_train_f1 = np.std(train_f1s)
    std_test_f1 = np.std(test_f1s)

    print(f"\nFinal Train Loss: {train_loss:.4f} | Final Test Loss: {test_loss:.4f}")
    print(f"Average Train Accuracy: {avg_train_acc*100:.2f}% (+/- {std_train_acc*100:.2f}%) | Average Test Accuracy: {avg_test_acc*100:.2f}% (+/- {std_test_acc*100:.2f}%)")
    print(f"Average Train F1 Score: {avg_train_f1*100:.2f}% ({std_train_f1*100:.2f}%) | Average Test F1 Score: {avg_test_f1*100:.2f}% ({std_test_f1*100:.2f}%)")

    print("\nTOTAL TIME TAKEN: {:.4f}s".format(time.time()-start0)) 
    print("AVG TIME PER EPOCH: {:.4f}s".format(np.mean(per_epoch_time)))
    
    # Writing on file
    with open(write_file_name + '.txt', 'w') as f:
        f.write("""Dataset: {},\nModel: {}\n\nparams={}\n\nnet_params={}\n\n{}\n\nTotal Parameters: {}\n\n
    FINAL RESULTS\n
    Avg Train Acc: {:.2f} +/- ({:.2f})
    Avg Test Acc: {:.2f} +/- ({:.2f})\n
    Avg Train F1: {:.2f} +/- ({:.2f})
    Avg Test F1: {:.2f} +/- ({:.2f})\n\n
    Total Time Taken: {:.4f} hrs\n
    Average Time Per Epoch: {:.4f} s\n\n\n"""\
      .format(DATASET_NAME, MODEL_NAME, params, net_params, model, net_params['total_param'],
              avg_train_acc*100, std_train_acc*100,
              avg_test_acc*100, std_test_acc*100,
              avg_train_f1*100, std_train_f1*100,
              avg_test_f1*100, std_test_f1*100,
              (time.time()-start0)/3600, np.mean(per_epoch_time)))
            


'''
# ---------------------------------------------------------------------------- #
#                                     MAIN                                     #
# ---------------------------------------------------------------------------- #
'''
def main():    
# ------------------------------- USER CONTROLS ------------------------------ #
    
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', help="Please give a config.json file with training/model/data/param details")
    parser.add_argument('--gpu_id', help="Please give a value for gpu id")
    parser.add_argument('--model', help="Please give a value for model name")
    parser.add_argument('--dataset', help="Please give a value for dataset name")
    parser.add_argument('--out_dir', help="Please give a value for out_dir")
    parser.add_argument('--seed', help="Please give a value for seed")
    parser.add_argument('--epochs', help="Please give a value for epochs")
    parser.add_argument('--batch_size', help="Please give a value for batch_size")
    parser.add_argument('--init_lr', help="Please give a value for init_lr")
    parser.add_argument('--lr_reduce_factor', help="Please give a value for lr_reduce_factor")
    parser.add_argument('--lr_schedule_patience', help="Please give a value for lr_schedule_patience")
    parser.add_argument('--min_lr', help="Please give a value for min_lr")
    parser.add_argument('--weight_decay', help="Please give a value for weight_decay")
    parser.add_argument('--print_epoch_interval', help="Please give a value for print_epoch_interval")    
    parser.add_argument('--L', help="Please give a value for L")
    parser.add_argument('--hidden_dim', help="Please give a value for hidden_dim")
    parser.add_argument('--out_dim', help="Please give a value for out_dim")
    parser.add_argument('--residual', help="Please give a value for residual")
    parser.add_argument('--edge_feat', help="Please give a value for edge_feat")
    parser.add_argument('--readout', help="Please give a value for readout")
    parser.add_argument('--n_heads', help="Please give a value for n_heads")
    parser.add_argument('--in_feat_dropout', help="Please give a value for in_feat_dropout")
    parser.add_argument('--dropout', help="Please give a value for dropout")
    parser.add_argument('--layer_norm', help="Please give a value for layer_norm")
    parser.add_argument('--batch_norm', help="Please give a value for batch_norm")
    parser.add_argument('--self_loop', help="Please give a value for self_loop")
    parser.add_argument('--max_time', help="Please give a value for max_time")
    parser.add_argument('--pos_enc_dim', help="Please give a value for pos_enc_dim")
    parser.add_argument('--lap_pos_enc', help="Please give a value for lap_pos_enc")
    parser.add_argument('--wl_pos_enc', help="Please give a value for wl_pos_enc")
    parser.add_argument('--kfold_splits', help="Please give a value for kfold_splits")
    args = parser.parse_args()
    with open(args.config) as f:
        config = json.load(f)
        
# ---------------------------------- device ---------------------------------- #

    if args.gpu_id is not None:
        config['gpu']['id'] = int(args.gpu_id)
        config['gpu']['use'] = True
    device = gpu_setup(config['gpu']['use'], config['gpu']['id'])
    # model, dataset, out_dir
    if args.model is not None:
        MODEL_NAME = args.model
    else:
        MODEL_NAME = config['model']
    if args.dataset is not None:
        DATASET_NAME = args.dataset
    else:
        DATASET_NAME = config['dataset']
    dataset = LoadData(DATASET_NAME)
    if args.out_dir is not None:
        out_dir = args.out_dir
    else:
        out_dir = config['out_dir']
        
# -------------------------------- parameters -------------------------------- #

    params = config['params']
    if args.seed is not None:
        params['seed'] = int(args.seed)
    if args.kfold_splits is not None:
        params['kfold_splits'] = int(args.kfold_splits)
    if args.epochs is not None:
        params['epochs'] = int(args.epochs)
    if args.batch_size is not None:
        params['batch_size'] = int(args.batch_size)
    if args.init_lr is not None:
        params['init_lr'] = float(args.init_lr)
    if args.lr_reduce_factor is not None:
        params['lr_reduce_factor'] = float(args.lr_reduce_factor)
    if args.lr_schedule_patience is not None:
        params['lr_schedule_patience'] = int(args.lr_schedule_patience)
    if args.min_lr is not None:
        params['min_lr'] = float(args.min_lr)
    if args.weight_decay is not None:
        params['weight_decay'] = float(args.weight_decay)
    if args.print_epoch_interval is not None:
        params['print_epoch_interval'] = int(args.print_epoch_interval)
    if args.max_time is not None:
        params['max_time'] = float(args.max_time)
        
# ---------------------------- network parameters ---------------------------- #

    net_params = config['net_params']
    net_params['device'] = device
    net_params['gpu_id'] = config['gpu']['id']
    net_params['batch_size'] = params['batch_size']
    if args.L is not None:
        net_params['L'] = int(args.L)
    if args.hidden_dim is not None:
        net_params['hidden_dim'] = int(args.hidden_dim)
    if args.out_dim is not None:
        net_params['out_dim'] = int(args.out_dim)   
    if args.residual is not None:
        net_params['residual'] = True if args.residual=='True' else False
    if args.edge_feat is not None:
        net_params['edge_feat'] = True if args.edge_feat=='True' else False
    if args.readout is not None:
        net_params['readout'] = args.readout
    if args.n_heads is not None:
        net_params['n_heads'] = int(args.n_heads)
    if args.in_feat_dropout is not None:
        net_params['in_feat_dropout'] = float(args.in_feat_dropout)
    if args.dropout is not None:
        net_params['dropout'] = float(args.dropout)
    if args.layer_norm is not None:
        net_params['layer_norm'] = True if args.layer_norm=='True' else False
    if args.batch_norm is not None:
        net_params['batch_norm'] = True if args.batch_norm=='True' else False
    if args.self_loop is not None:
        net_params['self_loop'] = True if args.self_loop=='True' else False
    if args.lap_pos_enc is not None:
        net_params['lap_pos_enc'] = True if args.pos_enc=='True' else False
    if args.pos_enc_dim is not None:
        net_params['pos_enc_dim'] = int(args.pos_enc_dim)
    if args.wl_pos_enc is not None:
        net_params['wl_pos_enc'] = True if args.pos_enc=='True' else False
        
# ------------------------------------ IGs ----------------------------------- #

    net_params['in_dim'] = torch.unique(dataset.train[0][0].ndata['feat'],dim=0).size(0)
    # net_params['in_dim'] = 0
    net_params['in_dim_edge'] = torch.unique(dataset.train[0][0].edata['feat'],dim=0).size(0)
    # net_params['in_dim_edge'] = 0
    classes = np.array([0, 1])
    classes = np.append(classes, np.unique(np.array(dataset.train[:][1])))
    num_classes = len(classes)
    net_params['n_classes'] = num_classes
    
    root_log_dir = out_dir + 'logs/' + MODEL_NAME + "_" + DATASET_NAME + "_GPU" + str(config['gpu']['id']) + "_" + time.strftime('%Hh%Mm%Ss_on_%b_%d_%Y')
    root_ckpt_dir = out_dir + 'checkpoints/' + MODEL_NAME + "_" + DATASET_NAME + "_GPU" + str(config['gpu']['id']) + "_" + time.strftime('%Hh%Mm%Ss_on_%b_%d_%Y')
    root_plots_dir = out_dir + 'plots/' + MODEL_NAME + "_" + DATASET_NAME + "_GPU" + str(config['gpu']['id']) + "_" + time.strftime('%Hh%Mm%Ss_on_%b_%d_%Y')
    write_file_name = out_dir + 'results/result_' + MODEL_NAME + "_" + DATASET_NAME + "_GPU" + str(config['gpu']['id']) + "_" + time.strftime('%Hh%Mm%Ss_on_%b_%d_%Y')
    write_config_file = out_dir + 'configs/config_' + MODEL_NAME + "_KFold_" + DATASET_NAME + "_GPU" + str(config['gpu']['id']) + "_" + time.strftime('%Hh%Mm%Ss_on_%b_%d_%Y')
    dirs = root_log_dir, root_ckpt_dir, root_plots_dir, write_file_name, write_config_file

    if not os.path.exists(out_dir + 'results'):
        os.makedirs(out_dir + 'results')
        
    if not os.path.exists(out_dir + 'configs'):
        os.makedirs(out_dir + 'configs')

    net_params['total_param'] = view_model_param(MODEL_NAME, net_params)
    train_test_pipeline(MODEL_NAME, dataset, params, net_params, dirs, classes)

main()    

























