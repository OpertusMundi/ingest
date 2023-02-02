import os
import pandas as pd
from valentine.algorithms import Coma
from yaml import safe_load
from valentine import valentine_match


class TopioWKSIngest:

    def __init__(self, threshold: float = 0.0):
        self.results = {}
        self.threshold = threshold
        self.df = ...

    def calculate_schema_similarities(self, df2):
        self.df = df2
        for f in os.listdir('schemata'):
            p = os.path.join('schemata', f)
            if os.path.isfile(p):
                with open(p, 'r') as schema_file:
                    column_names = list(pd.json_normalize(safe_load(schema_file)['attributes'])['name'])
                    df = pd.DataFrame(columns=column_names)
                    matcher = Coma()
                    matches = {match: sim for match, sim in valentine_match(df, df2, matcher).items()
                               if sim > self.threshold}
                    cleaned_matches = {match[0][1]: match[1][1] for match in matches.keys()}
                    self.results[f] = (len(cleaned_matches), column_names, cleaned_matches)
        self.results = {k: v for k, v in sorted(self.results.items(), key=lambda item: -item[1][0])}

    def get_schema_similarities(self):
        similarity_percentages = {k: round(v[0] / len(self.df.columns), 2) for k, v in self.results.items()}
        return similarity_percentages

    def ingest_with_max_similarity(self):
        _, column_names, cleaned_matches = list(self.results.values())[0]
        df = pd.DataFrame(columns=column_names)
        for df1_col, df2_col in cleaned_matches.items():
            df[df1_col] = self.df[df2_col]
        return df, pd.DataFrame(list(zip(list(cleaned_matches.keys()), list(cleaned_matches.values()))),
                                columns=['Ingested', 'Original'])

    def ingest_with_user_selection(self, schema_name: str):
        if schema_name not in self.results:
            return "Invalid schema name"
        else:
            _, column_names, cleaned_matches = self.results[schema_name]
            df = pd.DataFrame(columns=column_names)
            for df1_col, df2_col in cleaned_matches.items():
                df[df1_col] = self.df[df2_col]
            return df, pd.DataFrame(list(zip(list(cleaned_matches.keys()), list(cleaned_matches.values()))),
                                    columns=['Ingested', 'Original'])

    def ingest_default(self):
        return self.df
