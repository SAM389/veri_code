import torch
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")
model = SentenceTransformer("all-MiniLM-L6-v2")
vectors = torch.tensor(model.encode(["hello world"]))
