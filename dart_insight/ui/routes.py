"""Official query-parameter routing; atomic writes create native browser history."""
from datetime import date
import re
import streamlit as st

VIEWS={'홈':'home','공시 탐색':'explore','공시 상세':'detail','공시 분석':'analysis','분석 기록':'history'}
SCOPES={'보관된 공시':'stored','최근 7일 공시':'recent','전체 기간 검색':'all'}
FIELDS={'v','scope','c','begin','end','ty','q','p','r','origin'}


def _signature(params):
    return tuple(sorted((k,str(v)) for k,v in params.items() if k in FIELDS))


def _home(): pass
def _explore(): pass
def _detail(): pass
def _analysis(): pass
def _history(): pass


def register_pages():
    functions=[_home,_explore,_detail,_analysis,_history]
    pages={title:st.Page(fn,title=title,url_path=path,default=path=='home')
           for (title,path),fn in zip(VIEWS.items(),functions)}
    selected=st.navigation(list(pages.values()),position='hidden')
    transition=st.session_state.pop('_route_transition',None)
    if transition and selected.title!=transition[0]:
        st.switch_page(pages[transition[0]],query_params=transition[1])
    selected.run()
    st.session_state['_route_pages']=pages
    st.session_state['_route_current']=selected.title
    return selected.title


def sync_route(selected=None):
    params=st.query_params.to_dict()
    if selected is not None and (params or selected!='홈'):
        params['v']=VIEWS[selected]
    signature=_signature(params)
    if st.session_state.get('_route_seen')==signature:
        return
    first='_route_seen' not in st.session_state
    st.session_state['_route_seen']=signature
    if first and not signature:
        return
    reverse={value:key for key,value in VIEWS.items()}
    page=reverse.get(params.get('v'),'홈')
    for key in ('api_company','api_begin','api_end','api_kind','api_keyword'):
        st.session_state.pop(key,None)
    st.session_state['ui_page']=page
    st.session_state['ui_section']=page if page in ('홈','공시 탐색','분석 기록') else None
    scope={value:key for key,value in SCOPES.items()}.get(params.get('scope'),'보관된 공시')
    st.session_state['explore_scope']=scope
    if params.get('begin') and params.get('end'):
        try:
            begin=date.fromisoformat(params['begin']);end=date.fromisoformat(params['end'])
            query={'company':params.get('c','')[:100],'begin':begin.isoformat(),'end':end.isoformat(),
                   'kind':params.get('ty','')[:1],'keyword':params.get('q','')[:100]}
            st.session_state['api_query']=query
        except (ValueError,TypeError):
            st.session_state.pop('api_query',None)
    elif params.get('scope')=='all':
        st.session_state.pop('api_query',None)
    try: number=max(1,min(1000000,int(params.get('p',1))))
    except (ValueError,TypeError):number=1
    st.session_state['api_page']=number
    receipt=params.get('r','')
    if re.fullmatch(r'[0-9]{14}',receipt):
        st.session_state['route_receipt']=receipt
        if page=='공시 분석':st.session_state['chosen_receipt']=receipt
        selected=st.session_state.get('recent_detail')
        if selected is not None and selected.rcept_no!=receipt:
            st.session_state.pop('recent_detail',None)
    else:
        st.session_state.pop('route_receipt',None)
        if page=='공시 상세':st.session_state.pop('recent_detail',None)
        if page=='공시 분석':st.session_state.pop('chosen_receipt',None)
    st.session_state['detail_origin']='api' if params.get('origin')=='api' else 'recent'
    if page=='공시 상세':
        st.session_state['recent_return']={'scope':scope,'filters':{key:st.session_state[key] for key in ('api_query','api_page') if key in st.session_state}}


def write_route():
    page=st.session_state.get('ui_page','홈')
    params={'v':VIEWS.get(page,'home')}
    if page in ('공시 탐색','공시 상세'):
        scope=st.session_state.get('recent_return',{}).get('scope') if page=='공시 상세' else st.session_state.get('explore_scope')
        params['scope']=SCOPES.get(scope,'all')
    if page in ('공시 탐색','공시 상세') and (params.get('scope')=='all' or st.session_state.get('detail_origin')=='api' and page=='공시 상세'):
        query=st.session_state.get('api_query',{})
        for key,name in [('c','company'),('begin','begin'),('end','end'),('ty','kind'),('q','keyword')]:
            if query.get(name):params[key]=str(query[name])
        params['p']=str(st.session_state.get('api_page',1))
    if page=='공시 상세':
        selected=st.session_state.get('recent_detail')
        if selected:params['r']=selected.rcept_no
        params['origin']=st.session_state.get('detail_origin','recent')
    elif page=='공시 분석' and st.session_state.get('chosen_receipt'):
        params['r']=st.session_state['chosen_receipt']
    pages=st.session_state.get('_route_pages',{})
    if page in pages and st.session_state.get('_route_current')!=page:
        st.session_state['_route_transition']=(page,params)
        return
    # Authentication is neither represented nor inferred here. No secret/role fields are serialized.
    if _signature(st.query_params.to_dict())!=_signature(params):
        st.query_params.from_dict(params)
    st.session_state['_route_seen']=_signature(params)
