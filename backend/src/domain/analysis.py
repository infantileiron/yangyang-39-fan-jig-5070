from collections import defaultdict
from statistics import mean
from scipy.stats import f


CELLS = (("低风","直边夹"),("低风","弧边夹"),("高风","直边夹"),("高风","弧边夹"))
COLUMNS = (
    ("label", "组别／效应"), ("n", "试片数"), ("warp", "平均翘边（mm）"),
    ("energy", "平均耗电（Wh）"), ("effect", "因子差值（mm）"),
    ("statistic", "F 值"), ("p", "p 值"), ("decision", "判读"),
)


def validate(row):
    if (row["speed"],row["jig"]) not in CELLS:
        raise ValueError("风档和夹具必须是预注册四格之一")


def analyze(rows, parameters):
    energy_limit = parameters.get("energy_limit",15)
    alpha = parameters.get("alpha",0.05)
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        validate(row)
        if row["trial"] in seen:
            raise ValueError(f"试片 {row['trial']} 有多条完工记录")
        seen.add(row["trial"])
        groups[(row["speed"],row["jig"])].append(row)
    sizes = [len(groups[cell]) for cell in CELLS]
    if min(sizes) < 2:
        raise ValueError("四组各需至少两张有效试片")
    balanced = len(set(sizes)) == 1
    warps = [mean(row["warp"] for row in groups[cell]) for cell in CELLS]
    energies = [mean(row["energy"] for row in groups[cell]) for cell in CELLS]
    n = sizes[0]
    residual_df = len(rows)-4
    sse = sum((row["warp"]-warp)**2 for cell,warp in zip(CELLS,warps) for row in groups[cell])
    mse = sse/residual_df
    speed = ((warps[2]+warps[3])-(warps[0]+warps[1]))/2
    jig = ((warps[1]+warps[3])-(warps[0]+warps[2]))/2
    interaction = (warps[3]-warps[2])-(warps[1]-warps[0])
    table, chart = [], []
    for (wind,frame),warp,energy,count in zip(CELLS,warps,energies,sizes):
        label = f"{wind}·{frame}"
        table.append(dict(label=label,n=count,warp=round(warp,3),energy=round(energy,3),
                          decision="符合能耗护栏" if energy <= energy_limit else "平均耗电超过护栏"))
        chart.append((label,round(warp,3)))
    for label,effect,scale in (("高风 − 低风",speed,1),("弧边夹 − 直边夹",jig,1),("风档与夹具交互",interaction,0.25)):
        statistic = n*effect**2*scale/mse if balanced and mse > 1e-12 else None
        p = float(f.sf(statistic,1,residual_df)) if statistic is not None else None
        explanation = "四格不平衡，保留水平均值待补齐" if not balanced else "组内变异近零，暂停检验"
        if p is not None:
            explanation = "越过显著线" if p <= alpha else "未越过显著线"
        table.append(dict(label=label,effect=round(effect,4),statistic=round(statistic,6) if statistic is not None else None,
                          p=round(p,8) if p is not None else None,decision=explanation))
    choices = [(warp, cell[0]+"·"+cell[1]) for cell,warp,energy in zip(CELLS,warps,energies) if energy <= energy_limit]
    if balanced and choices:
        title = f"{min(choices)[1]} 进入下批试制复核"
    elif balanced:
        title = "四个组合都超过平均能耗护栏"
    else:
        title = "需补齐四格试片后再签核"
    return report(title,
                  f"能耗护栏为每片 {energy_limit:g} Wh，交互差为 {interaction:+.2f} mm。交互显著时按风档解释夹具效果。",
                  [("完成试片",len(rows),"片"),("护栏内组合",len(choices),"组"),("交互差",round(interaction,3),"mm")],
                  COLUMNS,table,"组合平均翘边",chart,"mm")


def seed():
    warps = (3.4,3.1,4.2,1.2)
    energies = (8.4,9.1,14.6,16.3)
    return [dict(trial=f"PX-{cell+1}{index+1:02d}",speed=wind,jig=frame,
                 warp=round(warps[cell]+(index*3%7-3)*0.13,2),
                 energy=round(energies[cell]+(index*5%7-3)*0.31,2))
            for cell,(wind,frame) in enumerate(CELLS) for index in range(7)]


def report(headline, summary, metrics, columns, rows, chart_label, chart_values, unit=""):
    return {
        "headline": headline, "summary": summary,
        "metrics": [{"label": label, "value": value, "unit": suffix} for label, value, suffix in metrics],
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": [{key: row.get(key) for key, _ in columns} for row in rows],
        "chart": {"label": chart_label, "unit": unit,
                  "points": [{"label": label, "value": value} for label, value in chart_values]},
    }
