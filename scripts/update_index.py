#!/usr/bin/env python3
import argparse,csv,json,math,os,time,urllib.parse,urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December']
DAYS=[31,28,31,30,31,30,31,31,30,31,30,31]
TARGET=50.0; GALLONS=100; UA=11.0; COP=2.2; BASE_KWH_DAY=.84

def get_json(url,timeout=40):
    req=urllib.request.Request(url,headers={'User-Agent':'ArcticSoakIndex/1.0 (+https://arcticsoak.com/methodology/)','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode('utf-8'))

def synthetic_normals(mean,amp):
    # Warmest around July; seed only, replaced by NOAA when online.
    return [round(mean + amp*math.cos(2*math.pi*(m-6)/12),1) for m in range(12)]

def load_cities():
    with open(ROOT/'data/cities.csv',newline='',encoding='utf-8') as f:return list(csv.DictReader(f))

def load_seed_rates():return json.loads((ROOT/'data/electricity_seed.json').read_text())

def load_existing():
    p=ROOT/'data/index.json'
    if p.exists():
        try:return json.loads(p.read_text())
        except:pass
    return {'cities':[]}

def fetch_eia_rates(api_key):
    if not api_key:return None
    params=[('api_key',api_key),('frequency','monthly'),('data[]','price'),('facets[sectorid][]','RES'),('sort[0][column]','period'),('sort[0][direction]','desc'),('offset','0'),('length','5000')]
    url='https://api.eia.gov/v2/electricity/retail-sales/data/?'+urllib.parse.urlencode(params)
    raw=get_json(url); rows=raw.get('response',{}).get('data',[]); latest={}
    for r in rows:
        st=r.get('stateid'); period=r.get('period'); price=r.get('price')
        if not st or len(st)!=2 or st in latest or price in (None,''):continue
        try:latest[st]=round(float(price)/100,4) # cents/kWh -> dollars/kWh
        except:continue
    return latest or None

def fetch_noaa_normals(lat,lon):
    for pad in (.22,.5):
        bbox=f'{lat+pad},{lon-pad},{lat-pad},{lon+pad}'
        q={'dataset':'normals-monthly-1991-2020','dataTypes':'MLY-TAVG-NORMAL','bbox':bbox,'format':'json','includeStationName':'true','includeStationLocation':'true','units':'standard'}
        url='https://www.ncei.noaa.gov/access/services/data/v1?'+urllib.parse.urlencode(q)
        try:rows=get_json(url)
        except Exception:continue
        if not isinstance(rows,list) or not rows:continue
        groups={}
        for r in rows:
            sid=r.get('STATION','unknown'); groups.setdefault(sid,[]).append(r)
        candidates=[]
        for sid,recs in groups.items():
            vals={}
            for idx,r in enumerate(recs):
                val=r.get('MLY-TAVG-NORMAL') or r.get('mly-tavg-normal')
                if val in (None,''):continue
                date=str(r.get('DATE',''))
                month=None
                for token in reversed(date.replace('/','-').split('-')):
                    if token.isdigit() and 1<=int(token)<=12:month=int(token);break
                if month is None and len(recs)==12:month=idx+1
                if month:
                    try:vals[month]=float(val)
                    except:pass
            if len(vals)>=10:
                r0=recs[0]
                try:slat=float(r0.get('LATITUDE',lat));slon=float(r0.get('LONGITUDE',lon));dist=(slat-lat)**2+(slon-lon)**2
                except:dist=999
                candidates.append((len(vals),-dist,sid,vals,r0.get('NAME') or r0.get('STATION_NAME') or sid))
        if candidates:
            candidates.sort(reverse=True);_,_,sid,vals,name=candidates[0]
            arr=[vals.get(m) for m in range(1,13)]
            # Fill rare missing months by interpolation / annual average.
            avg=sum(v for v in arr if v is not None)/sum(v is not None for v in arr)
            arr=[round(v if v is not None else avg,1) for v in arr]
            return arr, sid, name
    return None,None,None

def fetch_recent(lat,lon):
    end=datetime.now(timezone.utc).date()-timedelta(days=2);start=end-timedelta(days=30)
    for pad in (.15,.35):
        bbox=f'{lat+pad},{lon-pad},{lat-pad},{lon+pad}'
        q={'dataset':'daily-summaries','dataTypes':'TAVG,TMAX,TMIN','bbox':bbox,'startDate':str(start),'endDate':str(end),'format':'json','includeStationName':'true','includeStationLocation':'true','units':'standard'}
        url='https://www.ncei.noaa.gov/access/services/data/v1?'+urllib.parse.urlencode(q)
        try:rows=get_json(url)
        except Exception:continue
        if not isinstance(rows,list) or not rows:continue
        groups={}
        for r in rows:groups.setdefault(r.get('STATION','unknown'),[]).append(r)
        best=None
        for sid,recs in groups.items():
            vals=[]
            for r in recs:
                try:
                    if r.get('TAVG') not in (None,''):v=float(r['TAVG'])
                    elif r.get('TMAX') not in (None,'') and r.get('TMIN') not in (None,''):v=(float(r['TMAX'])+float(r['TMIN']))/2
                    else:continue
                    vals.append(v)
                except:pass
            if len(vals)>=15:
                r0=recs[0]
                try:slat=float(r0.get('LATITUDE',lat));slon=float(r0.get('LONGITUDE',lon));dist=(slat-lat)**2+(slon-lon)**2
                except:dist=999
                cand=(len(vals),-dist,sum(vals)/len(vals),sid,r0.get('NAME') or sid)
                if best is None or cand>best:best=cand
        if best:return round(best[2],1),best[3],best[4]
    return None,None,None

def monthly_model(temps,rate):
    out=[];annual_kwh=0;annual_cost=0;weighted_delta=0
    for i,temp in enumerate(temps):
        delta=max(float(temp)-TARGET,0)
        cooling=(UA*delta*24/3412/COP)
        kwh=(BASE_KWH_DAY+cooling)*DAYS[i]
        cost=kwh*rate
        annual_kwh+=kwh;annual_cost+=cost;weighted_delta+=delta*DAYS[i]
        out.append({'month':MONTHS[i],'temp_f':round(float(temp),1),'kwh':round(kwh,1),'cost':round(cost,2)})
    avg_delta=weighted_delta/365
    climate=max(0,100*(1-min(avg_delta/35,1)))
    cost_score=max(0,100*(1-min(max(annual_cost-35,0)/180,1)))
    score=.75*climate+.25*cost_score
    peak=max(out,key=lambda x:x['cost'])
    return out,round(annual_kwh,1),round(annual_cost,2),round(score,1),peak['month'],round(avg_delta,1)

def fetch_products():
    urls=['https://inhousewellness.com/collections/cold-plunge/products.json?limit=250','https://inhousewellness.com/collections/cold-plunge-cooling-system/products.json?limit=250']
    products=[];seen=set()
    for url in urls:
        try:raw=get_json(url)
        except Exception:continue
        for p in raw.get('products',[]):
            handle=p.get('handle');title=p.get('title','')
            if not handle or handle in seen:continue
            seen.add(handle)
            prices=[]
            for v in p.get('variants',[]):
                try:prices.append(float(v.get('price')))
                except:pass
            img=None
            if p.get('images'):img=p['images'][0].get('src')
            products.append({'title':title,'url':f'https://inhousewellness.com/products/{handle}','price':min(prices) if prices else None,'image':img,'source':'InHouse Wellness'})
    return products[:12] if products else None

def esc(s):return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

def page_shell(title,desc,body,canonical):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><meta name="description" content="{esc(desc)}"><link rel="canonical" href="https://arcticsoak.com{canonical}"><link rel="stylesheet" href="/assets/styles.css"><meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}"><meta property="og:type" content="website"><meta property="og:url" content="https://arcticsoak.com{canonical}"></head><body><div class="topbar"><div class="wrap"><span>Cold Plunge Climate & Cost Index</span><span>NOAA climate · EIA electricity</span></div></div><nav class="nav"><div class="wrap"><a class="brand" href="/">ARCTIC<span>SOAK</span></a><div class="navlinks"><a href="/cities/">Cities</a><a href="/rankings/">Rankings</a><a href="/calculators/ice/">Calculators</a><a href="/methodology/">Methodology</a></div></div></nav>{body}<footer class="footer"><div class="wrap"><div><a class="brand" href="/">ARCTIC<span>SOAK</span></a><p>Independent climate-and-cost modeling for cold plunge owners.</p></div><div><p><a href="/data/cold-plunge-index.csv">Download the dataset (CSV)</a> · <a href="/methodology/">Methodology</a></p><p class="tiny">Estimates are planning models, not manufacturer performance guarantees or medical advice. Product links may be commercial links to InHouse Wellness.</p></div></div></footer></body></html>'''

def generate_city(c):
    months=''.join(f'<div class="month"><span>{m["month"][:3]}</span><b>{m["temp_f"]:.0f}°</b><small>${m["cost"]:.0f}</small></div>' for m in c['months'])
    recent=f'''<div class="metric"><small>Recent 30-day avg</small><strong>{c['recent_temp_f']}°F</strong></div>''' if c.get('recent_temp_f') is not None else '<div class="metric"><small>Recent 30-day avg</small><strong>—</strong></div>'
    freeze=sum(1 for m in c['months'] if m['temp_f']<32)
    body=f'''<header class="page-hero"><div class="wrap"><div class="crumb"><a href="/">ArcticSoak</a> / <a href="/cities/">Cities</a> / {esc(c['city'])}</div><div class="eyebrow">Cold Plunge Index · {esc(c['state'])}</div><h1>{esc(c['city'])}, {esc(c['state'])}</h1><p class="lede">What does it take to maintain a 50°F outdoor cold plunge here? ArcticSoak combines climate normals with state electricity rates to estimate cooling demand and operating cost.</p></div></header><main class="wrap"><section class="city-dashboard"><div class="score-card"><div class="reading-label">ArcticSoak Score</div><div class="score-ring"><strong>{round(c['score'])}</strong><small>out of 100</small></div><p>Higher scores indicate an easier, lower-cost climate for maintaining the reference plunge at 50°F.</p></div><div class="metrics"><div class="metric"><small>Estimated annual electricity</small><strong>{c['annual_kwh']:.0f} kWh</strong></div><div class="metric"><small>Estimated annual cost</small><strong>${c['annual_cost']:.0f}</strong></div><div class="metric"><small>Peak cooling month</small><strong>{c['peak_month'][:3]}</strong></div><div class="metric"><small>Residential rate</small><strong>{c['rate']*100:.1f}¢</strong></div>{recent}<div class="metric"><small>Months avg below freezing</small><strong>{freeze}</strong></div></div></section><section class="section"><div class="section-head"><div><div class="eyebrow">Monthly profile</div><h2>Cooling burden through the year</h2></div><p>Monthly values use NOAA 1991–2020 normals when available. Costs use the latest state residential rate loaded from EIA or the bundled fallback rate.</p></div><div class="month-grid">{months}</div></section><section class="section article"><h2>What this means in {esc(c['city'])}</h2><p>The reference model assumes a covered, insulated 100-gallon outdoor plunge maintained at 50°F, with continuous low-power circulation and a chiller operating at an assumed coefficient of performance. It is designed for city-to-city comparison, not to predict the exact power draw of a specific product.</p><p>{'Cold-weather operation may require freeze protection, winterization, or equipment rated for freezing conditions.' if freeze else 'This climate does not show a monthly-normal average below freezing in the current model, though individual freezing days can still occur.'}</p><p><a class="btn" href="/calculators/cost/?city={c['slug']}">Model your own plunge →</a> <a class="btn ghost" href="https://inhousewellness.com/collections/cold-plunge" rel="sponsored">Browse cold plunges →</a></p></section></main>'''
    p=ROOT/'cities'/c['slug'];p.mkdir(parents=True,exist_ok=True);(p/'index.html').write_text(page_shell(f'Cold Plunge Cost in {c["city"]}, {c["state"]} | ArcticSoak',f'Estimate the electricity cost and climate difficulty of maintaining a 50°F cold plunge in {c["city"]}, {c["state"]}.',body,f'/cities/{c["slug"]}/'))

def generate_rankings(cities):
    rdir=ROOT/'rankings';rdir.mkdir(exist_ok=True)
    def table(items,metric):
        rows=''.join(f'<tr><td class="rank-num">{i+1}</td><td><a class="city-link" href="/cities/{c["slug"]}/">{esc(c["city"])}, {esc(c["state"])}</a></td><td><span class="score">{round(c["score"])}</span></td><td>${c["annual_cost"]:.0f}</td></tr>' for i,c in enumerate(items))
        return f'<div class="panel"><table class="rank-table"><thead><tr><th>#</th><th>City</th><th>Score</th><th>Est. annual cost</th></tr></thead><tbody>{rows}</tbody></table></div>'
    best=sorted(cities,key=lambda x:x['score'],reverse=True)
    expensive=sorted(cities,key=lambda x:x['annual_cost'],reverse=True)
    cheapest=sorted(cities,key=lambda x:x['annual_cost'])
    body=f'''<header class="page-hero"><div class="wrap"><div class="eyebrow">National Index</div><h1>Cold plunge city rankings</h1><p class="lede">Compare major U.S. cities by climate burden and estimated electricity cost for the same reference cold plunge.</p></div></header><main class="wrap"><section class="section"><h2>Best climates for cooling</h2>{table(best[:30],'score')}</section><section class="section"><h2>Highest estimated operating cost</h2>{table(expensive[:30],'cost')}</section><section class="section"><h2>Lowest estimated operating cost</h2>{table(cheapest[:30],'cost')}</section></main>'''
    (rdir/'index.html').write_text(page_shell('U.S. Cold Plunge City Rankings | ArcticSoak','Rank major U.S. cities by ArcticSoak Score and estimated cold plunge electricity cost.',body,'/rankings/'))

def generate_cities_index(cities):
    cards=''.join(f'<a class="tool" href="/cities/{c["slug"]}/"><div class="num">{esc(c["state"])}</div><h3>{esc(c["city"])}</h3><p>Score {round(c["score"])} · ${c["annual_cost"]:.0f}/yr estimated</p></a>' for c in sorted(cities,key=lambda x:x['city']))
    body=f'''<header class="page-hero"><div class="wrap"><div class="eyebrow">City database</div><h1>Cold plunge conditions by city</h1><p class="lede">Climate, electricity rates and reference operating-cost estimates for major U.S. cities.</p></div></header><main class="wrap"><section class="section"><div class="tool-grid">{cards}</div></section></main>'''
    (ROOT/'cities/index.html').write_text(page_shell('Cold Plunge Cost by U.S. City | ArcticSoak','Explore cold plunge climate and electricity-cost estimates by city.',body,'/cities/'))

def generate_sitemap(cities):
    paths=['/','/cities/','/rankings/','/calculators/ice/','/calculators/chiller/','/calculators/cost/','/methodology/']+[f'/cities/{c["slug"]}/' for c in cities]
    xml='<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'+''.join(f'<url><loc>https://arcticsoak.com{p}</loc></url>\n' for p in paths)+'</urlset>'
    (ROOT/'sitemap.xml').write_text(xml)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--offline',action='store_true');args=ap.parse_args()
    cities_cfg=load_cities();seed_rates=load_seed_rates();existing=load_existing();existing_by={c.get('slug'):c for c in existing.get('cities',[])}
    rates=seed_rates.copy();rate_source='Bundled planning rates'
    if not args.offline:
        try:
            fresh=fetch_eia_rates(os.getenv('EIA_API_KEY'))
            if fresh:rates.update(fresh);rate_source='U.S. EIA monthly residential retail price'
        except Exception as e:print('EIA update failed:',e)
    results=[]
    for n,row in enumerate(cities_cfg,1):
        lat=float(row['lat']);lon=float(row['lon']);slug=row['slug'];normal_source='Seed approximation';station=None;station_name=None
        temps=None
        if not args.offline:
            try:temps,station,station_name=fetch_noaa_normals(lat,lon)
            except Exception as e:print('NOAA normals failed',slug,e)
        if temps:normal_source='NOAA 1991–2020 monthly normals'
        else:
            old=existing_by.get(slug,{})
            old_months=old.get('months',[])
            if old_months and old.get('normal_source','').startswith('NOAA'):temps=[m['temp_f'] for m in old_months];normal_source=old['normal_source'];station=old.get('noaa_station');station_name=old.get('noaa_station_name')
            else:temps=synthetic_normals(float(row['annual_mean_f']),float(row['amplitude_f']))
        rate=float(rates.get(row['state'],.16));months,kwh,cost,score,peak,avg_delta=monthly_model(temps,rate)
        recent=None;recent_station=None;recent_name=None
        if not args.offline:
            try:recent,recent_station,recent_name=fetch_recent(lat,lon)
            except Exception as e:print('NOAA recent failed',slug,e)
        results.append({'city':row['city'],'state':row['state'],'slug':slug,'lat':lat,'lon':lon,'score':score,'annual_kwh':kwh,'annual_cost':cost,'rate':rate,'peak_month':peak,'avg_cooling_delta_f':avg_delta,'months':months,'recent_temp_f':recent,'normal_source':normal_source,'noaa_station':station,'noaa_station_name':station_name,'recent_station':recent_station,'recent_station_name':recent_name})
        print(f'[{n}/{len(cities_cfg)}] {slug}: score {score}')
        if not args.offline:time.sleep(.08)
    results.sort(key=lambda x:x['score'],reverse=True)
    for i,c in enumerate(results,1):c['rank']=i
    products=json.loads((ROOT/'data/products.json').read_text())
    if not args.offline:
        try:
            p=fetch_products()
            if p:products=p;(ROOT/'data/products.json').write_text(json.dumps(products,indent=2))
        except Exception as e:print('Product refresh failed:',e)
    now=datetime.now(timezone.utc)
    data={'generated_at':now.isoformat(),'updated_label':now.strftime('%B %d, %Y'),'reference_model':{'target_f':TARGET,'gallons':GALLONS,'effective_ua_btu_hr_f':UA,'chiller_cop':COP,'base_kwh_day':BASE_KWH_DAY},'climate_source':'NOAA NCEI 1991–2020 Monthly Climate Normals where refresh succeeded; seed approximations otherwise','recent_source':'NOAA NCEI Daily Summaries, recent 30-day average where available','electricity_source':rate_source,'cities':results,'products':products}
    (ROOT/'data/index.json').write_text(json.dumps(data,indent=2))
    with open(ROOT/'data/cold-plunge-index.csv','w',newline='',encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(['rank','city','state','arcticsoak_score','annual_kwh_est','annual_cost_est','electricity_rate_per_kwh','peak_month','recent_30d_avg_f','climate_source'])
        for c in results:w.writerow([c['rank'],c['city'],c['state'],c['score'],c['annual_kwh'],c['annual_cost'],c['rate'],c['peak_month'],c['recent_temp_f'] if c['recent_temp_f'] is not None else '',c['normal_source']])
    for c in results:generate_city(c)
    generate_rankings(results);generate_cities_index(results);generate_sitemap(results)
    print('Generated',len(results),'city pages')
if __name__=='__main__':main()
