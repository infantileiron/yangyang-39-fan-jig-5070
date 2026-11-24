"""演示用的台账补充与读数更正（确定性、无数据库依赖）。"""
from src.constants.enums import CorrectionStatus


def demo_extras():
    px201 = dict(trial='PX-201', speed='低风', jig='弧边夹', warp=2.71, energy=8.17)
    duplicate = dict(px201)  # 补抄耗电后留下的第二条完工记录
    verified = dict(
        trial='PX-201', basis={'warp': 2.71, 'energy': 8.17}, corrected={'energy': 8.5},
        status=CorrectionStatus.VERIFIED, verified_by='supervisor@demo.local',
        note='电表复核后核实，合并两条完工记录')
    pending_a = dict(
        trial='PX-207', basis={'warp': 3.23, 'energy': 8.79}, corrected={'warp': 5.9},
        status=CorrectionStatus.PENDING, note='复测抄表 A：翘边读数存疑')
    pending_b = dict(
        trial='PX-207', basis={'warp': 3.23, 'energy': 8.79}, corrected={'warp': 4.6},
        status=CorrectionStatus.PENDING, note='复测抄表 B：同一依据的另一种读数')
    return px201, duplicate, [verified, pending_a, pending_b]
