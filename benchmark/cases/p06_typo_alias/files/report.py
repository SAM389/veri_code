import pandsa as pd


def summary(path: str):
    return pd.read_csv(path).describe()
