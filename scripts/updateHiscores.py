import pandas as pd
import json
import requests
from urllib.request import Request, urlopen
import math

def getUsernamesFromClan(clan: str) -> list[str]:
    data = pd.read_csv('http://services.runescape.com/m=clan-hiscores/members_lite.ws?clanName='+ clan.replace(" ","%20").replace(" ","%20"),encoding='cp1252')
    return data[data.columns[0]].tolist()

def combatLevel(attLvl: int, strLvl: int, mgcLvl: int, rngLvl: int, necLvl: int, defLvl: int, conLvl: int, pryLvl: int, sumLvl: int) -> float:
    offensive = 13/10 * max( (attLvl + strLvl), 2*mgcLvl, 2*rngLvl, 2*necLvl )
    defensive = defLvl + conLvl + math.floor( pryLvl / 2 ) + math.floor( sumLvl / 2 )
    level = (offensive+defensive)/4
    return round(level,3)

def getRunemetricsData(url: str) -> str:
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    # Try a few times...
    for i in range(5):
        try:
            res = requests.get(urlopen( req).geturl())
            RMdata = json.loads(res.text)
            break
        except:
            continue

    return RMdata

def calculateLevel(xp: float) -> int:
    L = [-1,0,83,174,276,388,512,650,801,969,1154,1358,1584,1833,2107,2411,2746,3115,3523,3973,4470,5018,5624,6291,7028,7842,8740,9730,10824,12031,13363,14833,16456,18247,20224,22406,24815,27473,30408,33648,37224,41171,45529,50339,55649,61512,67983,75127,83014,91721,101333,111945,123660,136594,150872,166636,184040,203254,224466,247886,273742,302288,333804,368599,407015,449428,496254,547953,605032,668051,737627,814445,899257,992895,1096278,1210421,1336443,1475581,1629200,1798808,1986068,2192818,2421087,2673114,2951373,3258594,3597792,3972294,4385776,4842295,5346332,5902831,6517253,7195629,7944614,8771558,9684577,10692629,11805606,13034431,14391160,15889109,17542976,19368992,21385073,23611006,26068632,28782069,31777943,35085654,38737661,42769801,47221641,52136869,57563718,63555443,70170840,77474828,85539082,94442737,104273167,999999999]
    for i in range(len(L)):
        if xp >= L[i] and xp < L[i+1]:
            return i;

def calculateEliteLevel(xp: float) -> int:
    L = [-1,0,830,1861,2902,3980,5126,6390,7787,9400,11275,13605,16372,19656,23546,28138,33520,39809,47109,55535,64802,77190,90811,106221,123573,143025,164742,188893,215651,245196,277713,316311,358547,404634,454796,509259,568254,632019,700797,774834,854383,946227,1044569,1149696,1261903,1381488,1508756,1644015,1787581,1939773,2100917,2283490,2476369,2679907,2894505,3120508,3358307,3608290,3870846,4146374,4435275,4758122,5096111,5449685,5819299,6205407,6608473,7028964,7467354,7924122,8399751,8925664,9472665,10041285,10632061,11245538,11882262,12542789,13227679,13937496,14672812,15478994,16313404,17176661,18069395,18992239,19945833,20930821,21947856,22997593,24080695,25259906,26475754,27728955,29020233,30350318,31719944,33129852,34580790,36073511,37608773,39270442,40978509,42733789,44537107,46389292,48291180,50243611,52247435,54303504,56412678,58575823,60793812,63067521,65397835,67785643,70231841,72737330,75303019,77929820,80618654,83370445,86186124,89066630,92012904,95025896,98106559,101255855,104474750,107764216,111125230,114558777,118065845,121647430,125304532,129038159,132849323,136739041,140708338,144758242,148889790,153104021,157401983,161784728,166253312,170808801,175452262,180184770,185007406,189921255,194927409,999999999]
    for i in range(len(L)):
        if xp >= L[i] and xp < L[i+1]:
            return i;

'''
clans = ["Hitpoint Hierarchy"]
usernames=[]
for clan in clans:
    usernames += getUsernamesFromClan(clan)

usernames += ["Powerhouse","Urekiam","Dragonheart","Myiah"]

usernames = [username.replace(" "," ") for username in usernames]
usernames = sorted(usernames, key=str.casefold)
'''
# Just for testing purposes
usernames=["Toasty Romeo","Mihawk","pet","Cordious"]

csv = open("./data/hiscores.csv", "w+")
empty = True
for username in usernames:
    totExp = 0
    conExp = 0
    RScore = 0
    QPoint = 0
    virLvl = 0
    
    cmbLvl = 1
    totLvl = 29
    attLvl = 1
    strLvl = 1
    mgcLvl = 1
    rngLvl = 1
    necLvl = 1
    defLvl = 1
    conLvl = 1
    pryLvl = 1
    sumLvl = 1
    
    HSURL = "https://secure.runescape.com/m=hiscore/index_lite.ws?player=" + username.replace(" ","%20")
    try:
        HSData = pd.read_csv(HSURL,encoding='cp1252',header=None)
    except Exception as e:
        # User could not be found on HiScores, skip!
        continue;
    
    lvlList = HSData[HSData.columns[1]].tolist()
    expList = HSData[HSData.columns[2]].tolist()

    RMdata = getRunemetricsData("https://apps.runescape.com/runemetrics/profile/profile?user=" + username.replace(" ","%20") + "&activities=0")
        
    if not "error" in RMdata:
        # Get data from RuneMetrics
        totExp = RMdata["totalxp"]
        totLvl = RMdata["totalskill"]
        for skill in RMdata["skillvalues"]:
            level = skill["level"]
            id = skill["id"]
            match id:
                case 0: attLvl = level
                case 1: defLvl = level
                case 2: strLvl = level
                case 3:
                    conLvl = level
                    conExp = skill["xp"]/10
                case 4: rngLvl = level
                case 5: pryLvl = level
                case 6: mgcLvl = level
                case 23: sumLvl = level
                case 28: necLvl = level
            if id != 26:
                virLvl += calculateLevel(skill["xp"]/10)
            else:
                virLvl += calculateEliteLevel(skill["xp"]/10)
    else:
        # Get data from Hiscores
        totExp = int(expList[0])
        totLvl = lvlList[0]
        conExp = expList[4]
    
        attLvl = lvlList[1]
        strLvl = lvlList[3]
        mgcLvl = lvlList[7]
        rngLvl = lvlList[5]
        necLvl = lvlList[29]
        defLvl = lvlList[2]
        conLvl = lvlList[4]
        pryLvl = lvlList[6]
        sumLvl = lvlList[24]
        virLvl = sum([(calculateLevel(expList[i]) if i != 28 else calculateEliteLevel(expList[i])) for i in range(1,30)])
        
        # Check if only unranked skill is Constitution
        if conExp == -1:
            if expList[1:30].count(-1) == 1:
                # We can fix this
                conLvl = totLvl - sum(lvlList[1:30]) + 1 # +1 to correct for also subtracting the incorrect 1 Consititution level
                conExp = totExp - sum(expList[1:30]) - 1 # -1 to correct for also subtracting the incorrect -1 Constitution xp
                virLvl += conLvl
            else:
                # We can't fix this
                continue;
    
    cmbLvl = combatLevel(attLvl, strLvl, mgcLvl, rngLvl, necLvl, defLvl, conLvl, pryLvl, sumLvl)
    
    RScore = HSData[HSData.columns[1]].tolist()[54]
    if RScore == -1:
        RScore = ""

    QPdata = getRunemetricsData("https://apps.runescape.com/runemetrics/quests?user=" + username.replace(" ","%20").replace(" ","%20"))
    
    if QPdata["quests"] != []:
        for quest in QPdata["quests"]:
            if quest["status"] == "COMPLETED":
                QPoint += quest["questPoints"]
    else:
        QPoint = ""
            
    
    if conExp < 2746:
        # Constitution is 15 or lower
        if not empty:
            csv.write("\n")
        else:
            empty = False
        csv.write(username+","+str(conLvl)+","+str(conExp)+","+f"{cmbLvl:4.3f}"+","+str(totLvl)+","+str(virLvl)+","+str(totExp)+","+str(RScore)+","+str(QPoint))
csv.close()
