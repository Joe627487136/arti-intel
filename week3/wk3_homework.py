import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable
from torch.utils.data import Dataset, DataLoader

import PIL.Image as Image

import torchvision
from torchvision import models, transforms, utils

import getimagenetclasses as ginc
import os, math, time
import numpy as np
import itertools


#####
#   Dataset subclass
#####

class Wk3Dataset(Dataset):
    def __init__(self, root_dir, file_prefix='ILSVRC2012_val_',
                 img_ext='.JPEG', val_ext='.xml', synset='synset_words.txt',
                 five_crop=False, data_limit=0, selector=None):
        '''
        NOTE: set up your root_dir directory to consist of 
        2 directories:
        - imagespart: where the images are
        - val: where the xml's are (for class values etc)
        '''
        self.meta = {'root_dir':root_dir,
                     'file_prefix':file_prefix,
                     'synset':synset,
                     'img_ext':img_ext, 'val_ext':val_ext,
                     'five_crop': five_crop}

        # assertion for the mentioned assumption
        assert os.path.exists(os.path.join(root_dir, 'imagespart'))
        assert os.path.exists(os.path.join(root_dir, 'val'))
        assert os.path.exists(os.path.join(root_dir, synset))

        # metadata
        self.classes = ginc.get_classes()
        _, s2i, s2d = ginc.parsesynsetwords(self.meta['synset'])
        self.dataset = [filename[len(file_prefix):-len(img_ext)]
                        for filename in os.listdir(os.path.join(root_dir, 'imagespart'))]
        if data_limit > 0:
            self.dataset = self.dataset[:data_limit]

        if selector is not None:
            self.dataset = [d for d, s in zip(self.dataset, selector) if s]

        self._rev_dataset = s2i 
        self.data_description = s2d

    def get_val_path(self, index):
        return os.path.join(self.meta['root_dir'], 'val',
                            self.meta['file_prefix'] + str(index).zfill(8) + self.meta['val_ext'])

    def get_img_path(self, index):
        return os.path.join(self.meta['root_dir'], 'imagespart',
                            self.meta['file_prefix'] + str(index).zfill(8) + self.meta['img_ext'])
        
    def __len__(self):
        return len(self.dataset)

    def __getitem__(self, idx):
        '''
        Only when __getitem__ is called should the
        code load the actual image
        '''
        # since filenames starts with 1, index should be incremented
        index = self.dataset[idx]
        # 1. get corresponding dataset metadata
        label, _ = ginc.parseclasslabel(self.get_val_path(index), self._rev_dataset)

        # index label
        label_vector = int(label)
        
        # 2. load the image file
        image = Image.open(self.get_img_path(index))
        image = self.transform_short(image)

        five_crop = self.meta['five_crop']

        if five_crop:
            image = self.transform_fivecrop(image)
            image = [transforms.ToTensor()(t) for t in image]
            image = [t.repeat([3,1,1]) if t.size()[0] == 1 else t
                     for t in image]
            image = torch.stack(image)
            # stack :- 5 [3, 224, 224] tensor into [5, 3, 224, 224]
            # cat:- 5 [3, 224, 224] tensor into [15, 224, 224]
        else:
            image = self.transform_centercrop(image)
            image = transforms.ToTensor()(image)
            if image.size()[0] == 1: image = image.repeat((3,1,1))

        itm = {'label':label_vector, 'image':image}
        return itm
        
    #   Transform functions - always returns a function
    def transform_short(self, image, short_size=280):
        '''
        Do the transformation:
        - resize till the shorter side is 280
        '''
        width, height = image.size
        ratio = short_size / min(width, height)
        new_size = (int(width*ratio), int(height*ratio)) 
        new_img = image.resize(new_size, Image.ANTIALIAS)
        return new_img

    def transform_centercrop(self, image, size=224):
        '''
        Do the transformation:
        - take the center crop of sizexsize
        '''
        width, height = image.size 
        left = (width // 2) - (size//2)
        upper = (height // 2) - (size//2)
        right = left + size
        lower = upper + size
        crop_image = image.crop((left, upper, right, lower))
        return crop_image

    def transform_fivecrop(self, image, size=224):
        width, height = image.size 
        center_crop = self.transform_centercrop(image, size=size)
        topleft_crop = image.crop((0, 0, size, size))
        topright_crop = image.crop((width - size, 0, width, size))
        botleft_crop = image.crop((0, height - size, size, height))
        botright_crop = image.crop((width - size, height - size, width, height))
        return [center_crop, topleft_crop, topright_crop, botleft_crop, botright_crop]


######
#   Training codes
######

def train_model(dataset, model, optimizer, num_epoch=10, validation=False):
    '''
    Train the given model through the epochs.
    if validation is false, should be training mode
    '''
    loader = DataLoader(dataset, batch_size=32, shuffle=False, num_workers=4)
    criterion=torch.nn.CrossEntropyLoss()
    model.train(not validation)
    five_crop = dataset.meta['five_crop']

    mode = 'Val' if validation else 'Train'

    for e in range(num_epoch):
        print('{} - Epoch {}..'.format(mode, e))
        epoch_start = time.clock()
        running_loss = 0.0
        running_corrects = 0 
        for data in loader:
            optimizer.zero_grad()
            inputs, labels = data['image'], data['label']
            if five_crop:
                # Handling 5 crop by unfolding
                _, _, channel, size, _ = inputs.size()
                inputs = inputs.reshape(-1, channel, size, size)
                labels = np.repeat(labels, 5)

            inputs, labels = Variable(inputs), Variable(labels)
            outputs = model(inputs)
            _, predictions = outputs.max(dim=1)
            # print(predictions)
            # print(labels)
            # print((predictions.cpu() == labels.cpu()))

            loss = criterion(outputs, labels) / len(dataset)
            if not validation:
                loss.backward()
                optimizer.step()

            running_loss += loss.item()
            running_corrects += (predictions == labels).sum().item()
        
        epoch_loss = running_loss 
        if five_crop: epoch_loss /= 5
        epoch_acc = running_corrects / float(len(dataset)) 
        if five_crop: epoch_acc /= 5

        epoch_time = time.clock() - epoch_start
        print("      >> Epoch loss {:.5f} accuracy {:.3f}        \
              in {:.4f}s".format(epoch_loss, epoch_acc, epoch_time))

    return model


# some test

def generate_train_valset(root_path, limit=0, val_portion=0.1, five_crop=False):
    """
    Randomly split a dataset into non-overlapping new datasets of given lengths.
    """
    wk3dataset = Wk3Dataset(root_path, data_limit=limit, five_crop=five_crop)
    current_limit =  len(wk3dataset)

    train_mask = np.ones(current_limit)
    train_mask[:int(current_limit * val_portion)] -= 1
    np.random.shuffle(train_mask)
    val_mask = 1 - train_mask

    wk3train = Wk3Dataset(root_path, selector=train_mask,
                          data_limit=limit, five_crop=five_crop)
    wk3val = Wk3Dataset(root_path, selector=val_mask,
                        data_limit=limit, five_crop=False) # validation set never has 5crop
    return wk3train, wk3val

def test_dataset(dataset, index=0):
    # testing dataset getitem
    ww = dataset[index]
    print('dataset length', len(dataset), ww['image'].size())

def run_training(five_crop=False, dataset_count=250):
    '''
    Run training with preset parameters.
    '''
    wk3train, wk3val = generate_train_valset('../datasets/imagenet_first2500/', five_crop=five_crop,
                                             limit=dataset_count)
    test_dataset(wk3train)
    test_dataset(wk3val)
    # return

    #model - use alexnet
    model_ft = models.alexnet(pretrained=True, num_classes=len(wk3train.classes))
    # unfreeze the last layer
    num_ftrs = model_ft.classifier[6].in_features
    features = list(model_ft.classifier.children())[:-1]
    features.extend([nn.Linear(num_ftrs, len(wk3train.classes))])
    model_ft.classifier = nn.Sequential(*features)
    #optimizer
    optimizer_ft = optim.SGD(model_ft.parameters(), lr=0.01, momentum=0.9)

    # training
    model_ft = train_model(wk3train, model_ft, optimizer_ft, num_epoch=2)
    # validation
    model_ft = train_model(wk3val, model_ft, optimizer_ft, num_epoch=1, validation=True)
    return wk3val, model_ft


def main():
    run_training(five_crop=True)

if __name__ == '__main__':
    main()
    
