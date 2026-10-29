"""Mock 化学 AI 模型：温度升高产率上升，浓度过高略有抑制。"""


class ChemModelMock:
    BASELINE_TEMP_C = 45.0
    BASELINE_YIELD = 0.55
    TEMP_SLOPE = 0.02
    CONC_PENALTY = 0.2

    def predict(self, substrate: str, temperature_c: float, concentration_mol_l: float) -> dict:
        raw = (
            self.BASELINE_YIELD
            + self.TEMP_SLOPE * (temperature_c - self.BASELINE_TEMP_C)
            - self.CONC_PENALTY * max(0.0, concentration_mol_l - 0.1)
        )
        yield_rate = round(min(0.99, max(0.01, raw)), 4)
        return {
            "model": "chem-mock-v1",
            "substrate": substrate,
            "temperature_c": temperature_c,
            "concentration_mol_l": concentration_mol_l,
            "yield_rate": yield_rate,
        }
