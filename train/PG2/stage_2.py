import os

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader


from ignite.engine import Engine, Events

from ignite.metrics import RunningAverage


import dataset.bone_dataset as dataset
import models.PG2 as PG2
from util.v import get_current_visuals_
from loss.mask_l1 import MaskL1Loss
from util.image_pool import ImagePool
from train.common_handler import warp_common_handler

FAKE_IMG_FNAME = 'epoch_{:04d}.png'
VAL_IMG_FNAME = 'train_img/epoch_{:04d}_{:04d}.png'


def _move_data_pair_to(device, data_pair):
    # move data to GPU
    for k in data_pair:
        if "path" in k:
            # do not move string
            continue
        else:
            data_pair[k] = data_pair[k].to(device)


def get_trainer(option, device):
    val_image_dataset = dataset.BoneDataset(
        "../DataSet/Market-1501-v15.09.15/bounding_box_test/",
        "data/market/test/pose_map_image/",
        "data/market/test/pose_mask_image/",
        "data/market-pairs-test.csv",
        random_select=True,
        random_select_size=5
    )

    val_image_loader = DataLoader(val_image_dataset, batch_size=4, num_workers=1)
    val_data_pair = next(iter(val_image_loader))
    _move_data_pair_to(device, val_data_pair)

    generator_1 = PG2.G1(3 + 18, repeat_num=5, half_width=True, middle_z_dim=64)
    generator_1.load_state_dict(torch.load(option.G1_path))
    generator_2 = PG2.G2(3 + 3, hidden_num=64, repeat_num=3, skip_connect=1)
    discriminator = PG2.NDiscriminator(in_channels=6)
    generator_1.to(device)
    generator_2.to(device)
    discriminator.to(device)

    optimizer_generator_2 = optim.Adam(generator_2.parameters(), lr=option.g_lr, betas=(option.beta1, option.beta2))
    optimizer_discriminator = optim.Adam(discriminator.parameters(), lr=option.d_lr, betas=(option.beta1, option.beta2))

    bce_loss = nn.BCELoss().to(device)
    mask_l1_loss = MaskL1Loss().to(device)

    mask_l1_loss_lambda = option.mask_l1_loss_lambda
    batch_size = option.batch_size
    output_dir = option.output_dir

    real_labels = torch.ones((batch_size, 1), device=device)
    fake_labels = torch.zeros((batch_size, 1), device=device)

    fake_pair_img_pool = ImagePool(50)

    def step(engine, batch):
        _move_data_pair_to(device, batch)
        condition_img = batch["P1"]
        condition_pose = batch["BP2"]
        target_img = batch["P2"]
        target_mask = batch["MP2"]

        # get generated img
        generator_1_img = generator_1(torch.cat([condition_img, condition_pose], dim=1))
        diff_img = generator_2(torch.cat([condition_img, generator_1_img], dim=1))
        generated_img = generator_1_img + diff_img

        # -----------------------------------------------------------
        # (1) Update G2 network: minimize L_bce + L_1
        optimizer_generator_2.zero_grad()

        # BCE loss
        pred_disc_fake_1 = discriminator(torch.cat([condition_img, generated_img], dim=1))
        generator_2_bce_loss = bce_loss(pred_disc_fake_1, real_labels)
        # MaskL1 loss
        generator_2_mask_l1_loss = mask_l1_loss(generated_img, target_img, target_mask)
        # total loss
        generator_2_loss = generator_2_bce_loss + mask_l1_loss_lambda * generator_2_mask_l1_loss
        # gradient update
        generator_2_loss.backward()
        optimizer_generator_2.step()

        # -----------------------------------------------------------
        # (2) Update D network: minimize L_bce
        optimizer_discriminator.zero_grad()
        # real loss
        real_pair_img = torch.cat([condition_img, target_img], dim=1)
        pred_disc_real_2 = discriminator(real_pair_img)
        discriminator_real_loss = bce_loss(pred_disc_real_2, real_labels)
        # fake loss
        fake_pair_img = torch.cat([condition_img, generated_img], dim=1)
        #fake_pair_img = fake_pair_img_pool.query(torch.cat([condition_img, generated_img], dim=1).data)
        pred_disc_fake_2 = discriminator(fake_pair_img.detach())
        discriminator_fake_loss = bce_loss(pred_disc_fake_2, fake_labels)
        # total loss
        discriminator_loss = (discriminator_fake_loss + discriminator_real_loss) * 0.5
        discriminator_loss.backward()
        # gradient update
        optimizer_discriminator.step()

        # -----------------------------------------------------------
        # (3) Collect train info

        if engine.state.iteration % 100 == 0:
            path = os.path.join(output_dir, VAL_IMG_FNAME.format(engine.state.epoch, engine.state.iteration))
            get_current_visuals_(path, batch, [generator_1_img, diff_img, generated_img])

        return {
            "pred": {
                "G_fake": pred_disc_fake_1.mean().item(),
                "D_fake": pred_disc_fake_2.mean().item(),
                "D_real": pred_disc_real_2.mean().item()
            },
            "loss": {
                "G_bce": generator_2_bce_loss.item(),
                "G_l1": generator_2_mask_l1_loss.item(),
                "G": generator_2_loss.item(),
                "D_real": discriminator_real_loss.item(),
                "D_fake": discriminator_fake_loss.item(),
                "D": discriminator_loss.item()
            },
        }

        # ignite objects

    trainer = Engine(step)

    # attach running average metrics
    monitoring_metrics = ['pred_D_fake', 'pred_D_real', 'loss_G',  'loss_D']
    RunningAverage(output_transform=lambda x: x["pred"]['G_fake']).attach(trainer, 'pred_G_fake')
    RunningAverage(output_transform=lambda x: x["pred"]['D_fake']).attach(trainer, 'pred_D_fake')
    RunningAverage(output_transform=lambda x: x["pred"]['D_real']).attach(trainer, 'pred_D_real')

    RunningAverage(output_transform=lambda x: x["loss"]['G']).attach(trainer, 'loss_G')
    RunningAverage(output_transform=lambda x: x["loss"]['G_bce']).attach(trainer, 'loss_G_bce')
    RunningAverage(output_transform=lambda x: x["loss"]['G_l1']).attach(trainer, 'loss_G_l1')

    RunningAverage(output_transform=lambda x: x["loss"]['D']).attach(trainer, 'loss_D')
    RunningAverage(output_transform=lambda x: x["loss"]['D_real']).attach(trainer, 'loss_D_real')
    RunningAverage(output_transform=lambda x: x["loss"]['D_fake']).attach(trainer, 'loss_D_fake')

    networks_to_save = dict(G2=generator_2, D=discriminator)

    def add_message(engine):
        message = " | G_loss(all/bce/l1): {:.4f}/{:.4f}/{:.4f}".format(
            engine.state.metrics["loss_G"],
            engine.state.metrics["loss_G_bce"],
            engine.state.metrics["loss_G_l1"]
        )
        message += " | D_loss(all/fake/real): {:.4f}/{:.4f}/{:.4f}".format(
            engine.state.metrics["loss_D"],
            engine.state.metrics["loss_D_fake"],
            engine.state.metrics["loss_D_real"]
        )
        message += " | Pred(G2_fake/D_fake/D_real/): {:.4f}/{:.4f}/{:.4f}".format(
            engine.state.metrics["pred_G_fake"],
            engine.state.metrics["pred_D_fake"],
            engine.state.metrics["pred_D_real"]
        )
        return message

    warp_common_handler(
        trainer,
        option,
        networks_to_save,
        monitoring_metrics,
        add_message,
        [FAKE_IMG_FNAME, VAL_IMG_FNAME]
    )

    @trainer.on(Events.EPOCH_COMPLETED)
    def save_example(engine):
        img_g1 = generator_1(torch.cat([val_data_pair["P1"], val_data_pair["BP2"]], dim=1))
        diff_map = generator_2(torch.cat([val_data_pair["P1"], img_g1], dim=1))
        img_g2 = diff_map + img_g1
        path = os.path.join(output_dir, FAKE_IMG_FNAME.format(engine.state.epoch))
        get_current_visuals_(path, val_data_pair, [img_g1,diff_map,img_g2])

    return trainer


def add_new_arg_for_parser(parser):
    parser.add_argument('--d_lr', type=float, default=0.00002)
    parser.add_argument('--g_lr', type=float, default=0.00002)
    parser.add_argument('--beta1', type=float, default=0.5)
    parser.add_argument('--beta2', type=float, default=0.999)
    parser.add_argument('--mask_l1_loss_lambda', type=float, default=10)
    parser.add_argument('--G1_path', type=str, default="checkpoints/G1.pth")
    parser.add_argument('--market1501', type=str, default="../DataSet/Market-1501-v15.09.15/")


def get_data_loader(opt):
    image_dataset = dataset.BoneDataset(
        os.path.join(opt.market1501, "bounding_box_train/"),
        "data/market/train/pose_map_image/",
        "data/market/train/pose_mask_image/",
        "data/market-pairs-train.csv",
        random_select=True
    )
    image_loader = DataLoader(image_dataset, batch_size=opt.batch_size, num_workers=8, pin_memory=True, drop_last=True)
    return image_loader