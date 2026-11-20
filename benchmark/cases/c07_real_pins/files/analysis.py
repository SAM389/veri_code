import numpy as np
import pandas as pd
import requests

frame = pd.DataFrame({"x": np.arange(3)})
print(requests.__version__, frame.shape)
