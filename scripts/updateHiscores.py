import pandas as pd
import json
import requests
from urllib.request import Request, urlopen
import math

def getUsernamesFromCsv() -> list[str]:
    data = pd.read_csv('usernames.csv',header=None)
    return data[data.columns[0]].tolist()

def combatLevel(attLvl: int, strLvl: int, mgcLvl: int, rngLvl: int, necLvl: int, defLvl: int, conLvl: int, pryLvl: int, sumLvl: int) -> float:
    offensive = 13/10 * max( (attLvl + strLvl), 2*mgcLvl, 2*rngLvl, 2*necLvl )
    defensive = defLvl + conLvl + math.floor( pryLvl / 2 ) + math.floor( sumLvl / 2 )
    level = (offensive+defensive)/4
    return round(level,3)

def getRunemetricsData(url: str) -> str:
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    RMdata=None
    # Try a few times...
    for i in range(10):
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
            return min(i,120);
            
usernames = getUsernamesFromCsv()

csv = open("data/hiscores.csv", "w+")
empty = True
for username in usernames:
    HSURL = "https://secure.runescape.com/m=hiscore/index_lite.ws?player=" + username.replace(" ","%20")
    useRM = False
    try:
        HSData = pd.read_csv(HSURL,encoding='cp1252',header=None)
        lvlList = HSData[HSData.columns[1]].tolist()
        expList = HSData[HSData.columns[2]].tolist()

        totExp = int(expList[0])
        totLvl = lvlList[0]
        conExp = int(expList[4])
        
        attLvl = lvlList[1]
        strLvl = lvlList[3]
        mgcLvl = lvlList[7]
        rngLvl = lvlList[5]
        necLvl = lvlList[29]
        defLvl = lvlList[2]
        conLvl = lvlList[4]
        pryLvl = lvlList[6]
        sumLvl = lvlList[24]
        virLvl = sum([(calculateLevel(expList[i]) if i != 27 else calculateEliteLevel(expList[i])) for i in range(1,30)])
        
        RScore = HSData[HSData.columns[1]].tolist()[54]
        if RScore == -1:
            RScore = ""
            
        # Check if only unranked skill is Constitution
        if conExp == -1 and expList[1:30].count(-1) == 1:
            # We can fix this with HiScores only
            conLvl = totLvl - sum(lvlList[1:30]) + 1 # +1 to correct for also subtracting the incorrect 1 Consititution level
            conExp = int(totExp - sum(expList[1:30]) - 1) # -1 to correct for also subtracting the incorrect -1 Constitution xp
            virLvl += conLvl
        elif expList[1:30].count(-1) >= 1:
            useRM = True
            
    except Exception as e:
            useRM = True

    if useRM:
        # Try using RuneMetrics
        RMdata = getRunemetricsData("https://apps.runescape.com/runemetrics/profile/profile?user=" + username.replace(" ","%20") + "&activities=0")
        if RMdata == None:
            print("Skipped "+f"{username:<12s}"+" (could not load RuneMetrics data after 10 tries)")
            continue;
        if "error" in RMdata:
            print("Should be removed from usernames: "+f"{username:<12s}"+" (could not confirm hp level)")
            continue;
        
        totExp = RMdata["totalxp"]
        totLvl = RMdata["totalskill"]
        virLvl = 0
        for skill in RMdata["skillvalues"]:
            level = skill["level"]
            id = skill["id"]
            match id:
                case 0: attLvl = level
                case 1: defLvl = level
                case 2: strLvl = level
                case 3:
                    conLvl = level
                    conExp = math.floor(skill["xp"]/10)
                case 4: rngLvl = level
                case 5: pryLvl = level
                case 6: mgcLvl = level
                case 23: sumLvl = level
                case 28: necLvl = level
            if id != 26:
                virLvl += calculateLevel(skill["xp"]/10)
            else:
                virLvl += calculateEliteLevel(skill["xp"]/10)
                
        RScore = ""
        
    # Filter out mains and skillers
    if conLvl > 15:
        print("Should be removed from usernames: "+f"{username:<12s}"+" (hp level is "+str(conLvl)+")")
        continue
    if max(attLvl, strLvl, mgcLvl, rngLvl, necLvl, defLvl, pryLvl, sumLvl) < 11:
        print("Should be removed from usernames: "+f"{username:<12s}"+" (skiller)")
        continue;
    
    cmbLvl = combatLevel(attLvl, strLvl, mgcLvl, rngLvl, necLvl, defLvl, conLvl, pryLvl, sumLvl)

    QPdata = getRunemetricsData("https://apps.runescape.com/runemetrics/quests?user=" + username.replace(" ","%20"))
    
    QPoint = 0
    if QPdata["quests"] != []:
        for quest in QPdata["quests"]:
            if quest["status"] == "COMPLETED":
                QPoint += quest["questPoints"]
    else:
        QPoint = ""
    
    # Subtract Constitution level and xp
    totLvl -= conLvl
    totExp -= conExp
    virLvl -= conLvl

    print(f"| {username:<12s} | {conLvl:>2} | {conExp:>4} | {cmbLvl:>7.3f} | {totLvl:>4} | {virLvl:>4} | {totExp:>10} | {RScore:>5} | {QPoint:>3} |")

    if not empty:
        csv.write("\n")
    else:
        empty = False
    csv.write(username+","+str(conLvl)+","+str(conExp)+","+str(cmbLvl)+","+str(totLvl)+","+str(virLvl)+","+str(totExp)+","+str(RScore)+","+str(QPoint))
csv.close()
