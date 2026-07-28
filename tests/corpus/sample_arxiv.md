# Attention Is All You Need

## Abstract

The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.

## 1 Introduction

Recurrent neural networks, long short-term memory in particular, have been firmly established.

## 2 Background

The goal of reducing sequential computation forms the foundation of several models.

## 3 Model Architecture

Most competitive neural sequence transduction models have an encoder-decoder structure.

## 3.1 Encoder and Decoder Stacks

The encoder is composed of a stack of N = 6 identical layers.

## 3.2 Attention

An attention function can be described as mapping a query and a set of key-value pairs.

## 3.2.1 Scaled Dot-Product Attention

We call our particular attention Scaled Dot-Product Attention.

![](images/_page_3_Figure_1.jpeg)

Figure 1: The Transformer - model architecture.

## 4 Why Self-Attention

In this section we compare various aspects of self-attention layers.

## References

[1] Dzmitry Bahdanau, Kyunghyun Cho, and Yoshua Bengio. Neural machine translation. 2014.
