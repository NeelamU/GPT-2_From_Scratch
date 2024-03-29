# -*- coding: utf-8 -*-
"""gpt-2-from-scratch.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/gist/NeelamU/5cd18dc86910de56b7dbca2257a83d06/gpt-2-from-scratch.ipynb
"""

import torch
import torch.nn as nn
from torch.nn import functional as F

import nltk
from nltk.corpus import brown
import numpy as np
import torch
import torch.nn as nn
from torch.nn.utils.rnn import pad_sequence
import matplotlib.pyplot as plt
import math

device = 'cuda' if torch.cuda.is_available() else 'cpu'

"""# Encode the dataset"""

### Dataset --> X_train, y_train block
def encode_dataset(vocab, sentences):
  encodings = np.empty((len(sentences), ), dtype=object)
  for x, sentence in enumerate(sentences):
    encodings[x] = encode(sentence)



  train_size = int(math.floor(0.9*len(encodings)))
  X_train = np.empty((train_size, ), dtype = object)
  y_train = np.empty((train_size, ),dtype = object )
  print(train_size)


  for x, sentence in enumerate(encodings[:train_size]): #gonna have to torchify this
    if len(encodings[x])>1: ## if only 1 word sentence then X_train will be None
      X_train[x] = encodings[x][:-1]
      y_train[x] = encodings[x][1:]


  return X_train, y_train

## BLOCK TO CALL BROWN DATASET, Define global encode/decode, Instantiate X_train, y_train

nltk.download('brown')
text = brown.raw()  # Get the raw text from the Brown corpus
sentences = brown.sents()
sentences = [' '.join(each_sent) for each_sent in sentences]
sentences = ' '.join(sentences)
print(sentences[:1000])

### create vocab:
words = sentences.split()
vocab = sorted(list(set(words)))
text = sentences
vocab_size = len(vocab)
print(vocab[500:550])
print(len(vocab))

"""# Simple Tokenizer"""

## tokenize the input

char_to_num = {ch:i for i, ch in enumerate(vocab)}
num_to_char = {i:ch for i, ch in enumerate(vocab)}

encode = lambda e: [char_to_num[ch] for ch in e.split()]
decode = lambda d:' '.join([num_to_char[ch] for ch in d])

print(sentences[:102])
print(encode(text[:102]))
print(decode(encode(text[:102])))

dataset = torch.tensor(encode(sentences), dtype = torch.long)
print(dataset[:1000])

split_percentage = 0.9
n = int(split_percentage*len(dataset))

train_set = dataset[:n]
val_set = dataset[n:]

block_size = 128 # num characters in each sentence passed into model
batch_size = 32 # how many batches of these sentences
embed_size = 384
head_size = 64
num_heads = 6
num_blocks = 6
dropout = 0.2

def get_batch(split):
  data = train_set if split == 'train' else val_set
  ints = torch.randint(len(data) - block_size, (batch_size, )) # take 4 random integers from dataset

  inputs = torch.stack([data[x:x+block_size] for x in ints]) # batch_size, block_size
  labels = torch.stack([data[x+1:x+block_size+1] for x in ints])
  inputs = inputs.to(device)
  labels = labels.to(device)

  return inputs, labels


ins, outs = get_batch('train')
print(ins)
print(outs)

"""# Implement a Single Head"""

class Head(nn.Module):
  def __init__(self, head_size):
    super().__init__()

    self.key = nn.Linear(embed_size, head_size, bias = False) # need bias = False to preserve auto regression (i.e. 0s in diagonals)
    self.query = nn.Linear(embed_size, head_size, bias = False)#key @ query == attention scores for every token with every other token for every context length for each of the batches
    self.value = nn.Linear(embed_size, head_size, bias = False)
    self.register_buffer('tril', torch.tril(torch.ones(block_size, block_size))) ## !!!
    self.dropout = nn.Dropout(dropout)

  def forward(self, res_stream ):
    batch, block, embed = res_stream.shape

    key = self.key(res_stream) # res_stream is batch x block x embed
    query = self.query(res_stream)



    # due to masking, the attention scores are autoregressive
    # that is to say, each context size, 1 to block size, can't see the future tokens
    attn_scores = query @ key.transpose(-2, -1) * embed **-0.5 #batch x block x block, and * head_size is the sqrt(dk)
    attn_scores = attn_scores.masked_fill(self.tril[:block, :block] == 0, float('-inf')) # lower triangle so tokens of each size are attended to, masked fill makes 0s to -infs
    attn_scores = F.softmax(attn_scores, dim = -1)
    attn_scores = self.dropout(attn_scores)

    values = self.value(res_stream)
    # DROPOUT HERE!
    # head_out preserves the autoregressive nature by virtue of matrix multiplication with attn_scores
    # that is, past values for tokens are matmuled, by future tokens are simply zeroed out do to the masking in attn_scores
    head_out = attn_scores @ values # batch x block x head

    return head_out

"""# Multi Head Attention"""

class MultiHeadAttention(nn.Module):
  def __init__(self, num_heads, head_size):
    super().__init__()

    #instantiate num_heads heads into a nn.ModuleList (a list for modules)
    self.heads = nn.ModuleList([Head(head_size) for _ in range(num_heads)])
    self.to_stream = nn.Linear(num_heads * head_size, embed_size) #bias False?
    self.dropout = nn.Dropout(dropout)
    ## ADD DROPOUT
  def forward(self, res_stream):

    # returns a list of the output of the heads concatenated together
    # since this feed forwards each head, and the output of Head.forward() is head_out of size batch, block, head_size --
    # and since torch.cat is operating on the last dimension (-1 -- head_size) --
    # the new multi head dimensional output will be batch, block, num_head * head_size
    # essentially each head concatenates what it learns from attn_scores @ values
    # then a linear layer projects the auto regression from multi heads to embedding size to be added back to stream
    multi_heads = torch.cat([h(res_stream) for h in self.heads], dim = -1) # batch, block, num_head * head_size
    attention_out = self.dropout(self.to_stream(multi_heads)) # batch x block x embed_size

    return attention_out

"""# The 4x MLP  Block"""

class FeedForwardMLP(nn.Module):
  def __init__(self, embed_size):
    super().__init__()
    self.MLP = nn.Sequential(
        nn.Linear(embed_size, embed_size*4),
        nn.ReLU(),
        nn.Linear(embed_size*4, embed_size),
        nn.Dropout(dropout)
        # ADD DROPOUT
    )

  def forward(self, attention_out):
    MLP_out = self.MLP(attention_out) # size batch x block x embed_size
    return MLP_out

"""# A Single Transformer MHA+MLP Block"""

class Block(nn.Module):
  def __init__(self, num_heads, head_size, embed_size ):
    super().__init__()
    self.multihead_attn_layer = MultiHeadAttention(num_heads, head_size)
    self.MLP_layer = FeedForwardMLP(embed_size)
    self.layernorm1 = nn.LayerNorm(embed_size)
    self.layernorm2 = nn.LayerNorm(embed_size)
    #self.LayerNorm

  def forward(self, res_stream ):

    attention_out = self.multihead_attn_layer(self.layernorm1(res_stream)) + res_stream ### + res_stream implements residual stream
    MLP_FF = self.MLP_layer(self.layernorm2(attention_out)) + attention_out ## + attention_out implements residual stream

    return MLP_FF

"""# Putting it all Together"""

class BigramLanguageModel(nn.Module):
  def __init__(self):
    super().__init__()
    self.token_embedding_table = nn.Embedding(vocab_size, embed_size) # embeddings are vocab_size
    self.position_embedding_table = nn.Embedding(block_size, embed_size)

    blocks = [Block(num_heads, head_size, embed_size) for _ in range(num_blocks)]
    self.TransformerBlocks = nn.Sequential(*blocks)

    # self.TransformerBlocks = nn.Sequential(*[Block(num_heads, head_size, embed_size)] for _ in range(num_blocks))
    # self.TransformerBlock1 = Block(num_heads, head_size, embed_size)
    # self.TransformerBlock2 = Block(num_heads, head_size, embed_size)
    # self.TransformerBlock3 = Block(num_heads, head_size, embed_size)
    # self.TransformerBlock4 = Block(num_heads, head_size, embed_size)
    # self.TransformerBlock5 = Block(num_heads, head_size, embed_size)
    # self.TransformerBlock6 = Block(num_heads, head_size, embed_size)
    # self.TransformerBlock7 = Block(num_heads, head_size, embed_size)
    # self.TransformerBlock8 = Block(num_heads, head_size, embed_size)

    self.layernorm_final = nn.LayerNorm(embed_size)

    self.unembedding = nn.Linear(embed_size, vocab_size)

  def forward(self, inputs, labels):
    batch, block = inputs.shape

    # embedding and position encoding

    embeddings = self.token_embedding_table(inputs) # of size batch_size, block_size, embed_size, in this case embed_size = vocab_size
    positions = self.position_embedding_table(torch.arange(block, device = device)) # this results in block_size, embed_size

    # print(embeddings.shape, 'emb')
    # print(positions.shape, 'position')
    res_stream = embeddings + positions # batch x block x embed -- different shapes add to make 4, 8, 32 i.e. 8, 32 + 4, 8, 32 = 4, 8, 32
    # TRANSFORMER BLOCK (multi-head attention, MLP layer, LayerNorm)
    to_stream = self.TransformerBlocks(res_stream) # to_stream size -- batch x block x embed

    to_logits = self.layernorm_final(to_stream) # last layernorm
    #unembedding -- from residual stream (embedding) to vocab_size logits
    logits = self.unembedding(to_logits) # batch x block x vocab
    # B, T, C = logits.shape
    # logits = logits.view(B*T, C)
    # targets = labels.view(B*T)
    # loss = F.cross_entropy(logits, targets)

    return logits

  def generate(self, input  , max_new_tokens):
    #index is batch_size * len_seq
    for i in range(max_new_tokens):

      input_forward = input[:, -block_size:]
      logits = self(input_forward, input_forward ) # input = labels is redundant

      logits = logits[:, -1, :] #batch, embed taken on the last character prediction

      probabilities = F.softmax(logits, dim = -1) # -1 means take softmax on last dimension i.e the embed dimension

      next_idx = torch.multinomial(probabilities, num_samples = 1)  # get idx of max probability

      input = torch.cat((input, next_idx), dim = 1) # size batch, block_size + 1


    return input

"""# Train it!"""

learning_rate = 1e-4

model = BigramLanguageModel()
model = model.to(device)
optimizer = torch.optim.AdamW(model.parameters(), lr = learning_rate)
loss_func = nn.CrossEntropyLoss()

block_size = 128 # num characters in each sentence passed into model
batch_size = 32 # how many batches of these sentences
embed_size = 768
head_size = 256
num_heads = 6
num_blocks = 12
dropout = 0.4

# TRAINING LOOP
import os
import numpy as np

os.environ['CUDA_LAUNCH_BLOCKING'] = "1"

loss = 0





steps = 5000
eval_iters = 25

print(sum(p.numel() for p in model.parameters())/1e6, 'M parameters')


for step in range(steps):
  if step % 200 == 0:
    losses_tr = []
    losses_val = []
    model.eval()
    with torch.no_grad():
      for split in ['train', 'val']:
        for k in range(eval_iters):
          inputs, labels = get_batch(split)

          logits =  model(inputs, labels)


          logits = logits.permute(0, 2, 1)
          loss = loss_func(logits, labels)
          if split == 'train':
            losses_tr.append(loss.cpu())
          else:
            losses_val.append(loss.cpu())

      loss_tr = np.array(losses_tr).mean()
      loss_val = np.array(losses_val).mean()

      print(f"step {step}: train loss {loss_tr:.4f}, validation loss {loss_val:.4f}")

  model.train()


  inputs, labels = get_batch('train')

  logits =  model(inputs, labels)




  logits = logits.permute(0, 2, 1)
  loss = loss_func(logits, labels)


  optimizer.zero_grad(set_to_none = True)
  loss.backward()
  optimizer.step()

"""# Generate some Text"""

testin, testout = get_batch('train')
the_in = testin.cpu().numpy().tolist()
for each in the_in:
  print(decode(each))
  break

out = model.generate(testin, 20
                     )
print('\n\n\n')
print('AI GENERATED TEXT:')
print('\n\n\n')
for each in out:
  sentence_pred = each.cpu().numpy().tolist()
  print(decode(sentence_pred))
  break # just one of the block_size samples

