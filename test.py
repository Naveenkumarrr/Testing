import streamlit as st
import plotly.express as px
import pandas as pd
import json

agg_trans = pd.read_csv('agg_trans.csv')
india_states = json.load(open("assets\\states_india.geojson", "r"))
id=0
state_id_map = {""}
for feature in india_states["features"]:
    feature['id'] = id
    state_id_map[feature["properties"]["ST_NM"]] = feature['id']
    id+=1

state_ids = sorted(state_id_map.items(), key=  lambda item: item[0])

#print(state_ids)
print(india_states['features'][0]['properties'])
# agg_trans['id'] = agg_trans['State'].map(state_id_map)
# agg_trans['State']=agg_trans['State']



#print(agg_trans['State'].unique())
states=sorted(agg_trans['State'].unique())
#print(states)
state_codes={}
state_names={}
for id,state in zip(state_ids,states):
    state_codes[state]=id[1]
    state_names[id[1]]=id[0]
agg_trans['State_id'] = agg_trans['State'].map(state_codes)
#agg_trans['State'] = agg_trans['State_id'].map(state_names)
lst=[]

print(state_codes)
st_trns=agg_trans.groupby('State')['Transaction_amount'].sum()
df=pd.DataFrame()
df['State']=st_trns.index
df['Transaction_amount']=st_trns.values
df['id']=df['State'].map(state_codes)
print(df.head())

fig = px.choropleth(
    df,
    geojson=india_states,
    locations='id',
    color='Transaction_amount',
    hover_name='State',
    projection='mercator',
    scope='asia',
    color_continuous_scale=['#f7fbff', '#ebf3fb', '#deebf7', '#d2ebf3', '#c6e3ef', '#b9daea', '#acd1e4']

)

fig.update_geos(fitbounds="locations", visible=False,)
#combining districts states and coropleth
fig.add_trace( fig.data[0])
fig.add_trace(fig.data[0])

fig.update_geos(fitbounds="locations", visible=False,)
# Display the choropleth map in Streamlit
st.plotly_chart(fig)

