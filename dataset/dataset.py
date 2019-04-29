import csv
import os
import numpy

import torch
from torch.utils.data import Dataset
from torchvision.datasets.folder import default_loader
from torchvision import transforms

DEFAULT_TRANS = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5])
])


class BoneDataset(Dataset):
    def __init__(self, image_folder, bone_folder, mask_folder, pair_list_path,
                 random_select=True, random_select_size=4000, use_flip=True,
                 loader=default_loader, transform=DEFAULT_TRANS, only_path=False):

        self.image_folder = image_folder
        self.bone_folder = bone_folder
        self.mask_folder = mask_folder

        self.use_flip = use_flip

        self.size, self.pairs = self.load_pair_list(pair_list_path)

        self.transform = transform
        self.loader = loader

        self.only_path = only_path

        self.random_select = random_select
        self.random_select_size = random_select_size

    def __repr__(self):
        return "<BoneDataset size: {} real_size: {} random_select: {}>".format(
            len(self), self.size, self.random_select
        )

    @staticmethod
    def load_pair_list(pair_list_path):
        assert os.path.isfile(pair_list_path)
        with open(pair_list_path, "r") as f:
            f_csv = csv.reader(f)
            next(f_csv)
            pair_list = [tuple(item) for item in f_csv]
            return len(pair_list), pair_list

    def load_bone_data(self, img_name, flip=False):
        bone_img = numpy.load(os.path.join(self.bone_folder, img_name + ".npy"))
        bone = torch.from_numpy(bone_img).float()  # h, w, c
        bone = bone.transpose(2, 0)  # c,w,h
        bone = bone.transpose(2, 1)  # c,h,w
        if flip:
            bone = bone.flip(dims=[-1])
        return bone

    def load_mask_data(self, img_name, flip=False):
        mask = torch.Tensor(numpy.load(os.path.join(self.mask_folder, img_name + ".npy")).astype(int))
        if flip:
            mask = mask.flip(dims=[-1])
        mask = mask.unsqueeze(0).expand(3, -1, -1)
        return mask

    def load_image_data(self, path, flip=False):
        try:
            img = self.loader(os.path.join(self.image_folder, path))
        except FileNotFoundError as e:
            print(path)
            raise e

        if self.transform is not None:
            img = self.transform(img)
        if flip:
            img = img.flip(dims=[-1])
        return img

    def __getitem__(self, input_idx):
        if self.random_select:
            index = torch.randint(0, self.size - 1, (1,)).item()
        else:
            index = input_idx

        img_p1_name, img_p2_name = self.pairs[index]

        if self.use_flip:
            flip = torch.randint(0, self.size - 1, (1,)).item() > (self.size - 1)/2
        else:
            flip = False

        if self.only_path:
            return {'P1_path': img_p1_name, 'P2_path': img_p2_name}

        img_p1 = self.load_image_data(img_p1_name, flip)
        img_p2 = self.load_image_data(img_p2_name, flip)
        bone_p1 = self.load_bone_data(img_p1_name, flip)
        bone_p2 = self.load_bone_data(img_p2_name, flip)
        mask_p1 = self.load_mask_data(img_p1_name, flip)
        mask_p2 = self.load_mask_data(img_p2_name, flip)

        return {'P1': img_p1, 'BP1': bone_p1, 'P1_path': img_p1_name, 'MP1': mask_p1,
                'P2': img_p2, 'BP2': bone_p2, 'P2_path': img_p2_name, 'MP2': mask_p2}

    def __len__(self):
        return self.random_select_size if self.random_select else self.size


if __name__ == '__main__':
    image_dataset = BoneDataset(
        "../DataSet/Market-1501-v15.09.15/bounding_box_train/",
        "data/market/train/pose_map_image/",
        "data/market/train/pose_mask_image/",
        "data/market-pairs-train.csv",
        random_select=True
    )
    print(len(image_dataset))
    print(image_dataset[0]["P1"][0][0])