import csv
import requests
import math

def download(url: str):
    response = requests.get(url, headers={'content-type':'application/json'})
    if response.status_code == 200:
        return response
    raise Exception("404")

# Get HiScores data (in array form)
def getHiScores(username: str):
    url = "https://secure.runescape.com/m=hiscore/index_lite.ws?player=" + username.replace(" ","%20")
    res = download(url).text
    rows = res.split("\n")[:-1]
    return [ [int(x) for x in row.split(",")] for row in rows ]

# Get RuneMetrics data (in dict form)
def getRuneMetrics(username: str):
    global RMFails
    url = "https://apps.runescape.com/runemetrics/profile/profile?user=" + username.replace(" ","%20") + "&activities=0"
    for _ in range(10):
        try:
            res = download(url)
            return res.json()
        except: 
            RMFails += [username]
            pass
    return {}

# Takes usernames from csv
def getUsernamesFromCsv() -> list[str]:
    orig = [_[0] for _ in csv.reader(open('data/usernames.csv'))]
    temp = set()
    srtd = []
    for user in orig:
        userLower = user.lower()
        if userLower not in temp:
            temp.add(userLower)
            srtd.append(user)
    srtd.sort(key=str.lower)
    return srtd

def combatLevel(attLvl: int, strLvl: int, mgcLvl: int, rngLvl: int, necLvl: int, defLvl: int, conLvl: int, pryLvl: int, sumLvl: int) -> float:
    offensive = 13/10 * max( (attLvl + strLvl), 2*mgcLvl, 2*rngLvl, 2*necLvl )
    defensive = defLvl + conLvl + math.floor( pryLvl / 2 ) + math.floor( sumLvl / 2 )
    level = (offensive+defensive)/4
    return round(level,3)

def calculateLevel(xp: int) -> int:
    L = [-1,0,83,174,276,388,512,650,801,969,1154,1358,1584,1833,2107,2411,2746,3115,3523,3973,4470,5018,5624,6291,7028,7842,8740,9730,10824,12031,13363,14833,16456,18247,20224,22406,24815,27473,30408,33648,37224,41171,45529,50339,55649,61512,67983,75127,83014,91721,101333,111945,123660,136594,150872,166636,184040,203254,224466,247886,273742,302288,333804,368599,407015,449428,496254,547953,605032,668051,737627,814445,899257,992895,1096278,1210421,1336443,1475581,1629200,1798808,1986068,2192818,2421087,2673114,2951373,3258594,3597792,3972294,4385776,4842295,5346332,5902831,6517253,7195629,7944614,8771558,9684577,10692629,11805606,13034431,14391160,15889109,17542976,19368992,21385073,23611006,26068632,28782069,31777943,35085654,38737661,42769801,47221641,52136869,57563718,63555443,70170840,77474828,85539082,94442737,104273167,999999999]
    for i in range(len(L)):
        if xp >= L[i] and xp < L[i+1]:
            return i;

def calculateEliteLevel(xp: int) -> int:
    L = [-1,0,830,1861,2902,3980,5126,6390,7787,9400,11275,13605,16372,19656,23546,28138,33520,39809,47109,55535,64802,77190,90811,106221,123573,143025,164742,188893,215651,245196,277713,316311,358547,404634,454796,509259,568254,632019,700797,774834,854383,946227,1044569,1149696,1261903,1381488,1508756,1644015,1787581,1939773,2100917,2283490,2476369,2679907,2894505,3120508,3358307,3608290,3870846,4146374,4435275,4758122,5096111,5449685,5819299,6205407,6608473,7028964,7467354,7924122,8399751,8925664,9472665,10041285,10632061,11245538,11882262,12542789,13227679,13937496,14672812,15478994,16313404,17176661,18069395,18992239,19945833,20930821,21947856,22997593,24080695,25259906,26475754,27728955,29020233,30350318,31719944,33129852,34580790,36073511,37608773,39270442,40978509,42733789,44537107,46389292,48291180,50243611,52247435,54303504,56412678,58575823,60793812,63067521,65397835,67785643,70231841,72737330,75303019,77929820,80618654,83370445,86186124,89066630,92012904,95025896,98106559,101255855,104474750,107764216,111125230,114558777,118065845,121647430,125304532,129038159,132849323,136739041,140708338,144758242,148889790,153104021,157401983,161784728,166253312,170808801,175452262,180184770,185007406,189921255,194927409,999999999]
    for i in range(len(L)):
        if xp >= L[i] and xp < L[i+1]:
            return i;

usernames = getUsernamesFromCsv()

hiscoresString = ""
usernamesString = ""
RMFails = 0
print(f"╔══════════════╦════╦══════╦══════╦══════╦════════════╦═════╦═══════╗")
print(f"║ DISPLAY NAME ║ HP ║ HPXP ║ TOTL ║ VIRT ║  TOTAL XP  ║ CMB ║ SCORE ║")
print(f"╠══════════════╬════╬══════╬══════╬══════╬════════════╬═════╬═══════╣")
for username in usernames:
    RScore = ""
    try:
        # Try using HiScores
        HSData = getHiScores(username)
        
        RScore = HSData[54][1]
        # Also check rank to prevent bug that incorrectly gives RuneScore
        if RScore == -1 or HSData[54][0] == -1: RScore = ""
        
        totLvl = HSData[0][1]
        totExp = HSData[0][2]
        
        lvlList = [HSData[_][1] for _ in range(1,30)]
        expList = [HSData[_][2] for _ in range(1,30)]
        
        if expList.count(-1) > 1:
            raise Exception("Too many unranked skills")
        elif expList.count(-1) == 1:
            # We can fix this!
            i = expList.index(-1)
            lvlList[i] = totLvl - sum(lvlList) + 1 # +1 to correct for subtracting the incorrect level set as 1
            expList[i] = totExp - sum(expList) - 1 # -1 to correct for subtracting the incorrect experience set as -1
    
    except:
        # Try using RuneMetrics
        RMData = getRuneMetrics(username)
        if not RMData:
            usernamesString += f"{username}\n" # Keep in list for future retry
            continue;
        if "error" in RMData:
            continue;
        
        totLvl = RMData["totalskill"]
        totExp = RMData["totalxp"]
        
        skills = sorted(RMData["skillvalues"], key=lambda d: d['id'])
        
        lvlList = [_["level"] for _ in skills]
        expList = [math.floor(_["xp"]/10) for _ in skills]
    
    finally:
        attLvl = lvlList[0]
        attExp = expList[0]
        
        strLvl = lvlList[2]
        strExp = expList[2]
        
        mgcLvl = lvlList[6]
        mgcExp = expList[6]
        
        rngLvl = lvlList[4]
        rngExp = expList[4]
        
        necLvl = lvlList[28]
        necExp = expList[28]
        
        defLvl = lvlList[1]
        defExp = expList[1]
        
        conLvl = lvlList[3]
        conExp = expList[3]
        
        pryLvl = lvlList[5]
        pryExp = expList[5]
        
        sumLvl = lvlList[23]
        sumExp = expList[23]

    # Filter out mains and skillers
    if conLvl > 15 or max(attLvl, strLvl, mgcLvl, rngLvl, necLvl, defLvl, pryLvl, sumLvl) < 11:
        continue;
            
    virLvl = sum([(calculateLevel(expList[i]) if i != 26 else calculateEliteLevel(expList[i])) for i in range(29)])
    
    cmbLvl = math.floor(combatLevel(attLvl, strLvl, mgcLvl, rngLvl, necLvl, defLvl, conLvl, pryLvl, sumLvl))

    # Subtract Constitution level and xp
    cmbLvlAdj = combatLevel(attLvl, strLvl,mgcLvl,rngLvl,necLvl,defLvl, 1, pryLvl, sumLvl)
    cmbExpAdj = attExp + strExp + mgcExp + rngExp + necExp + defExp + pryExp + sumExp
    totLvlAdj = totLvl - conLvl + 1
    totExpAdj = totExp - conExp
    virLvlAdj = virLvl - conLvl + 1

    print(f"║ {username:<12s} ║ {conLvl:>2} ║ {conExp:>4} ║ {totLvl:>4} ║ {virLvl:>4} ║ {totExp:>10} ║ {cmbLvl:>3} ║ {RScore:>5} ║")
    usernamesString += f"{username}\n"
    hiscoresString += f"{username},{conLvl},{conExp},{totLvl},{totLvlAdj},{virLvl},{virLvlAdj},{totExp},{totExpAdj},{cmbLvl},{cmbLvlAdj:>4.3f},{cmbExpAdj},{RScore}\n"

print(f"╚══════════════╩════╩══════╩══════╩══════╩════════════╩═════╩═══════╝\n\nRuneMetrics fails: {RMFails}")
with open("data/usernames.csv", "w+") as usernamesFile:
    usernamesFile.write(usernamesString)
with open("data/hiscores.csv", "w+") as hiscoresFile:
    hiscoresFile.write(hiscoresString)
