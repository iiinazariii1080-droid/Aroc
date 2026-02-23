import math
from app.types import XarmMoveWithJointsDictParams, Joints
def calculate_difference(position1, position2):
    """
    Рассчитывает евклидово расстояние между двумя позициями.
    """
    return math.sqrt(
        (position1["j1"] - position2["j1"])**2 +
        (position1["j2"] - position2["j2"])**2 +
        (position1["j3"] - position2["j3"])**2 +
        (position1["j4"] - position2["j4"])**2 +
        (position1["j5"] - position2["j5"])**2 +
        (position1["j6"] - position2["j6"])**2
    )

def find_closest_position(current_position):
    """
    Находит ближайшую сохранённую позицию к текущей позиции.
    """

    for saved_position in poses:
        difference = calculate_difference(current_position['joints'], saved_position['joints'])
        if difference < 5:
            return saved_position
    return current_position

def get_XarmMoveWithJointsDictParams_with_box_num(box_number):
    if box_number<1 or box_number>2:
        raise ("Box not found")
    position1 = next((p for p in poses if p["name"] == "TRANSPORT_STEP_1"), None)
    position2 = next((p for p in poses if p["name"] == "BOX_STEP_1"), None)
    position3 = next((p for p in poses if p["name"] == "BOX_"+str(box_number)+"_STEP_2"), None)
    params = XarmMoveWithJointsDictParams(
        points=[
            Joints(**{k: float(v) for k,v in position1['joints'].items()}),
            Joints(**{k: float(v) for k,v in position2['joints'].items()}),
            Joints(**{k: float(v) for k,v in position3['joints'].items()}),
        ],
        velocity_percent=50.0,
        blocking=True,
        reset_faults=True
    )
    return params

def get_XarmMoveWithJointsDictParams_for_transport_position():
    position1 = next((p for p in poses if p["name"] == "TRANSPORT_STEP_1"), None)
    position2 = next((p for p in poses if p["name"] == "TRANSPORT_STEP_2"), None)
    params = XarmMoveWithJointsDictParams(
        points=[
            Joints(**{k: float(v) for k,v in position1['joints'].items()}),
            Joints(**{k: float(v) for k,v in position2['joints'].items()}),
        ],
        velocity_percent=50.0,
        blocking=True,
        reset_faults=True,
    )
    return params

def get_XarmMoveWithJointsDictParams_for_move_to_center():
    position1 = next((p for p in poses if p["name"] == "READY_STEP_1"), None)
    position2 = next((p for p in poses if p["name"] == "READY_STEP_2"), None)
    position3 = next((p for p in poses if p["name"] == "READY_SECTION_CENTER"), None)
    params = XarmMoveWithJointsDictParams(
        points=[
            Joints(**{k: float(v) for k,v in position1['joints'].items()}),
            Joints(**{k: float(v) for k,v in position2['joints'].items()}),
            Joints(**{k: float(v) for k,v in position3['joints'].items()}),
        ],
        velocity_percent=50.0,
        blocking=True,
        reset_faults=True
    )
    return params
    
poses = [
    {'name':"TARGET_DAVE_TAKE", 'joints': {
        "j1": 90,
        "j2": 11,
        "j3": -63,
        "j4": 230,
        "j5": 87,
        "j6": -218        
    }},
    {'name':"TARGET_DAVE_CAMERA_FOCUS", 'joints': {
        "j1": 120,
        "j2": 17,
        "j3": -57,
        "j4": 250,
        "j5": 112,
        "j6": -218        
    }},
    {'name':"MEASURE_DESK_STEP_1", 'joints': {
        "j1": 33,
        "j2": 0,
        "j3": -19,
        "j4": 174,
        "j5": 10,
        "j6": 5        
    }},

    {'name':"MEASURE_DESK_STEP_2", 'joints': {
        "j1": 27,
        "j2": 81,
        "j3": -63,
        "j4": 171,
        "j5": 44,
        "j6": 5
    }},
    
    {'name':"BOX_STEP_1", 'joints': {
        "j1": 32,
        "j2": -89,
        "j3": -15,
        "j4": -1,
        "j5": 62,
        "j6": -173
    }},

    {'name':"BOX_1_STEP_2", 'joints': {
        "j1": 124,
        "j2": 50,
        "j3": -31,
        "j4": 64,
        "j5": -34,
        "j6": 35
    }},
    
    {'name':"BOX_1_STEP_3", 'joints': {
        "j1": 99,
        "j2": 82,
        "j3": -55,
        "j4": 38,
        "j5": -45,
        "j6": -56
    }},

    {'name':"BOX_2_STEP_2", 'joints': {
        "j1": -46,
        "j2": 39,
        "j3": -39,
        "j4": 101,
        "j5": 30,
        "j6": -175
    }},
    {'name':"FORWARD_WELCOME_START", 'joints': {
        "j1": 28,
        "j2": -68,
        "j3": -106,
        "j4": 2,
        "j5": 57,
        "j6": -5
    }},
    {'name':"FORWARD_WELCOME_UP", 'joints': {
        "j1": 28,
        "j2": -66,
        "j3": -105,
        "j4": 2,
        "j5": 27,
        "j6": -5
    }},
    {'name':"FORWARD_WELCOME_DOWN", 'joints': {
        "j1": 30,
        "j2": -75,
        "j3": -100,
        "j4": 2,
        "j5": 100,
        "j6": -5
    }},


    {'name':"RIGHT_WELCOME_START", 'joints': {
        "j1": 88,
        "j2": -53,
        "j3": -80,
        "j4": 23,
        "j5": 44,
        "j6": -55
    }},
    {'name':"RIGHT_WELCOME_UP", 'joints': {
        "j1": 115,
        "j2": -55,
        "j3": -75,
        "j4": 54,
        "j5": 14,
        "j6": -91
    }},
    {'name':"RIGHT_WELCOME_DOWN", 'joints': {
        "j1": 82,
        "j2": -52,
        "j3": -86,
        "j4": 13,
        "j5": 60,
        "j6": -47
    }},


    {'name':"LEFT_WELCOME_START", 'joints': {
        "j1": -34,
        "j2": -55,
        "j3": -77,
        "j4": -22,
        "j5": 37,
        "j6": 45
    }},
    {'name':"LEFT_WELCOME_UP", 'joints': {
        "j1": -56,
        "j2": -55,
        "j3": -72,
        "j4": -35,
        "j5": 8,
        "j6": 64
    }},

    {'name':"LEFT_WELCOME_DOWN", 'joints': {
        "j1": -41,
        "j2": -50,
        "j3": -92,
        "j4": 10,
        "j5": 73,
        "j6": 25
    }},


    {'name':"MEASURE_DESK", 'joints': {
        "j1": 57,
        "j2": 7,
        "j3": -49,
        "j4": -230,
        "j5": -21,
        "j6": 246
    }},

    {'name':"BOX", 'joints': {
        "j1": 62,
        "j2": 26,
        "j3":-67,
        "j4": -57,
        "j5": 16,
        "j6": 84
    }},

    {'name':"READY_STEP_1", 'joints': {
        "j1": 34,
        "j2": -87,
        "j3":-6,
        "j4":101,
        "j5": 115,
        "j6": -114
    }},
    {'name':"READY_STEP_2", 'joints': {
        "j1": 29,
        "j2": -37,
        "j3":3,
        "j4":172,
        "j5": 158,
        "j6": -179
    }},
    {'name':"READY_SECTION_1", 'joints': {
        "j1": 95,
        "j2": 7,
        "j3": -35,
        "j4": 235,
        "j5": 106,
        "j6": -206
    }},
    {'name':"READY_SECTION_2", 'joints': {
        "j1": 66,
        "j2": -25,
        "j3": -3,
        "j4": 211,
        "j5": 97,
        "j6": -196
    }},
    {'name':"READY_SECTION_3", 'joints': {
        "j1": -28,
        "j2": -5,
        "j3": -20,
        "j4": 131,
        "j5": 105,
        "j6": -158
    }},
    {'name':"READY_SECTION_4", 'joints': {
        "j1": -40,
        "j2": 18,
        "j3":-50,
        "j4": 122,
        "j5": 105,
        "j6": -148
    }},
    {'name':"READY_SECTION_CENTER", 'joints': {
        "j1": 29,
        "j2": -38,
        "j3": 3,
        "j4": 179,
        "j5": 84,
        "j6": -181
    }},
    {'name':"DRIVING", 'joints': {
        "j1": 268.2,
        "j2": 2.6,
        "j3": -77.7,
        "j4": -34.1,
        "j5": 141.4,
        "j6": 108.2
    }},
    {'name':"TRANSPORT_STEP_1", 'joints': {
        "j1": 32,
        "j2": -85,
        "j3":-5,
        "j4":100,
        "j5": 80,
        "j6": -113
    }},
    {'name':"COLLISION", 'joints': {
        "j1": -56,
        "j2": -104,
        "j3":-7,
        "j4": 47,
        "j5": 120,
        "j6": -88
    }},
    {'name':"TRANSPORT_STEP_2", 'joints': {
        "j1": 31,
        "j2": -85,
        "j3":-15,
        "j4":77,
        "j5": 151,
        "j6": -45
    }},
    {'name':"START_SCAN_POSITION", 'joints': {
        "j1": -36,
        "j2": 66,
        "j3": -95,
        "j4": 124,
        "j5": 106,
        "j6": -153
    }}
]
