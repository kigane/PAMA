import argparse
import logging
import os
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.utils.data as data
import wandb
from PIL import Image, ImageFile
from torchvision.utils import save_image
from tqdm import tqdm

from net import Net
from utils import (DEVICE, FlatFolderDataset, InfiniteSamplerWrapper,
                   adjust_learning_rate, plot_grad_flow, test_transform,
                   train_transform)

Image.MAX_IMAGE_PIXELS = None  
ImageFile.LOAD_TRUNCATED_IMAGES = True


def train(args):
    logging.basicConfig(filename='training.log',
                    format='%(asctime)s %(levelname)s: %(message)s', 
                    level=logging.INFO, 
                    datefmt='%Y-%m-%d %H:%M:%S')
    wandb.init(project="PAMA",  # wandb项目名称
            #    group=config.group,  # 记录的分组，用于过滤
            #    job_type=config.job_type,  # 记录的类型，用于过滤
               config=args)  # config必须接受字典对象
    columns = ['iter', 'content', 'style', 'result']
    test_table = wandb.Table(columns=columns)  # 创建表格，确定表头
    mes = "current pid: " + str(os.getpid())
    print(mes)
    logging.info(mes)
    model = Net(args)
    model.train()
    # device_ids = [0, 1]
    # model = nn.DataParallel(model, device_ids=device_ids)
    model = model.to(DEVICE)

    tf = train_transform()
    content_dataset = FlatFolderDataset(args.content_folder, tf)
    style_dataset = FlatFolderDataset(args.style_folder, tf)
    content_iter = iter(data.DataLoader(
                        content_dataset, batch_size=args.batch_size,
                        sampler=InfiniteSamplerWrapper(content_dataset),
                        num_workers=args.num_workers))
    style_iter = iter(data.DataLoader(
                      style_dataset, batch_size=args.batch_size,
                      sampler=InfiniteSamplerWrapper(style_dataset),
                      num_workers=args.num_workers))

    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    for img_index in (pbar := tqdm(range(args.iterations))):
        optimizer.zero_grad()
        Ic = next(content_iter).to(DEVICE)
        Is = next(style_iter).to(DEVICE)
        
        loss = model(Ic, Is)
        pbar.set_postfix({
            'loss': loss.item()
        })
        wandb.log({
            "loss": loss.item()
        }, step=img_index+1)
        loss.sum().backward()
        
        #plot_grad_flow(GMMN.named_parameters())
        optimizer.step()

        if (img_index+1)%args.log_interval == 0:
            print("saving...")
            mes = "iteration: " + str(img_index+1) + " loss: "  + str(loss.sum().item())
            logging.info(mes)
            model.save_ckpts()
            model.eval()
            model.args.training = False
            with torch.no_grad():
                Ics = model(Ic, Is)
            for i in range(Ics.shape[0]):
                test_table.add_data(
                    img_index+1, 
                    wandb.Image(tensor2im(Ic[i])),
                    wandb.Image(tensor2im(Is[i])),
                    wandb.Image(tensor2im(Ics[i])),
                )
            model.train()
            model.args.training = True
            adjust_learning_rate(optimizer, img_index, args)
    # wandb的table并不能每次更新
    wandb.log({"results_table": test_table})  # 记录表格

def tensor2im(tensor):
    img = (tensor.permute(1,2,0) + 1.) / 2. * 255
    img = img.data.cpu().float().numpy()
    return img.astype(np.uint8)

def eval(args):
    mes = "current pid: " + str(os.getpid())
    print(mes)
    logging.info(mes)
    model = Net(args)
    model.eval()
    model = model.to(DEVICE)
    
    tf = test_transform()
    if args.run_folder == True:
        content_dir = args.content 
        style_dir = args.style
        for content in os.listdir(content_dir):
            for style in os.listdir(style_dir):
                name_c = content_dir + content
                name_s = style_dir + style
                Ic = tf(Image.open(name_c)).to(DEVICE)
                Is = tf(Image.open(name_s)).to(DEVICE)
                Ic = Ic.unsqueeze(dim=0)
                Is = Is.unsqueeze(dim=0)
                with torch.no_grad():
                    Ics = model(Ic, Is)

                name_cs = "ics/" + os.path.splitext(content)[0]+"--"+style 
                save_image(Ics[0], name_cs)
    else:
        Ic = tf(Image.open(args.content)).to(DEVICE)
        Is = tf(Image.open(args.style)).to(DEVICE)

        Ic = Ic.unsqueeze(dim=0)
        Is = Is.unsqueeze(dim=0)
        
        with torch.no_grad():
            Ics = model(Ic, Is)

        name_cs = "ics.jpg"
        save_image(Ics[0], name_cs)
    

def main():
    main_parser = argparse.ArgumentParser(description="main parser")
    subparsers = main_parser.add_subparsers(title="subcommands", dest="subcommand")

    main_parser.add_argument("--pretrained", action="store_true",
                                   help="whether to use the pre-trained checkpoints")
    main_parser.add_argument("--requires_grad", type=bool, default=True,
                                   help="set to True if the model requires model gradient")

    train_parser = subparsers.add_parser("train", help="training mode parser")
    train_parser.add_argument("--training", type=bool, default=True)
    train_parser.add_argument("--iterations", type=int, default=160000,
                                  help="total training epochs (default: 160000)")
    train_parser.add_argument("--batch_size", type=int, default=8,
                                  help="training batch size (default: 8)")
    train_parser.add_argument("--num_workers", type=int, default=8,
                                  help="iterator threads (default: 8)")
    train_parser.add_argument("--lr", type=float, default=1e-4, help="the learning rate during training (default: 1e-4)")
    train_parser.add_argument("--content_folder", type=str, required = True, 
                                  help="the root of content images, the path should point to a folder")
    train_parser.add_argument("--style_folder", type=str, required = True,
                                  help="the root of style images, the path should point to a folder")
    train_parser.add_argument("--log_interval", type=int, default=10000,
                                  help="number of images after which the training loss is logged (default: 20000)") 

    train_parser.add_argument("--w_content1", type=float, default=12, help="the stage1 content loss weight")
    train_parser.add_argument("--w_content2", type=float, default=9, help="the stage2 content loss weight")
    train_parser.add_argument("--w_content3", type=float, default=7, help="the stage3 content loss weight")
    train_parser.add_argument("--w_remd1", type=float, default=2, help="the stage1 remd loss weight")
    train_parser.add_argument("--w_remd2", type=float, default=2, help="the stage2 remd loss weight")
    train_parser.add_argument("--w_remd3", type=float, default=2, help="the stage3 remd loss weight")
    train_parser.add_argument("--w_moment1", type=float, default=2, help="the stage1 moment loss weight")
    train_parser.add_argument("--w_moment2", type=float, default=2, help="the stage2 moment loss weight")
    train_parser.add_argument("--w_moment3", type=float, default=2, help="the stage3 moment loss weight")
    train_parser.add_argument("--color_on", type=str, default=True, help="turn on the color loss")
    train_parser.add_argument("--w_color1", type=float, default=0.25, help="the stage1 color loss weight")
    train_parser.add_argument("--w_color2", type=float, default=0.5, help="the stage2 color loss weight")
    train_parser.add_argument("--w_color3", type=float, default=1, help="the stage3 color loss weight")

    
    eval_parser = subparsers.add_parser("eval", help="evaluation mode parser")
    eval_parser.add_argument("--training", type=bool, default=False)
    eval_parser.add_argument("--run_folder", action="store_true")
    eval_parser.add_argument("--content", type=str, default="./content/",
                                  help="content image you want to stylize")
    eval_parser.add_argument("--style", type=str, default="./style/",
                                  help="style image for stylization")

    args = main_parser.parse_args()



    if args.subcommand is None:
        print("ERROR: specify either train or eval")
        sys.exit(1)
    if args.subcommand == "train":
        train(args)
    
    else:
        eval(args)

if __name__ == "__main__":
    main()
