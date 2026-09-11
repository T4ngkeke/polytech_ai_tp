from scripts.compare_worker_layout import compare


def row(page,ok,calls):
    trace=[{'seconds':1}]*calls
    audit={'pages':[{'trace':trace}] if ok else [],'page_results':[{'trace':trace}]}
    return {'file':'x.pdf','page':page,'round':1,'status':'accepted' if ok else 'needs_review','seconds':calls,'audit':audit}


def test_call_saving_cannot_hide_new_failed_pages():
    result=compare([row(1,True,4),row(2,True,4)],[row(1,True,2),row(2,False,1)])
    assert result['common_success_mean_calls']=={'before':4,'after':2}
    assert result['after_total_calls']==3
    assert not result['mechanical_gate_passed']


def test_native_formula_coverage_is_not_visual_formula_success():
    from scripts.worker_visual_gold import check_page
    r={'file':'main-2.pdf','page':55,'audit':{'pages':[{'groups':[
        {'kind':'text','text':'Nombre = (−1)S × (1:M) × 2E−biais','visual':None}]}]}}
    assert check_page(r)['formula_structure_preserved'] is False


def test_uml_page_needs_a_figure_description_and_its_printed_class_names():
    from scripts.worker_visual_gold import check_page
    names='Employee\nManager\nProgrammer'
    r={'file':'Heritage.pdf','page':8,'audit':{'pages':[{'groups':[{'kind':'text','text':names,'visual':None}]}]}}
    assert check_page(r)['figure_described'] is False
    r['audit']['pages'][0]['groups'][0]={'kind':'figure','text':'Hiérarchie de classes.\n'+names,
        'visual':{'kind':'figure','description':'Hiérarchie de classes.'}}
    assert check_page(r)=={'figure_described':True,'class_names_kept':True}


def test_partial_pages_are_not_counted_as_automatic_acceptance():
    from scripts.check_worker_layout import aggregate
    rows=[{'status':'partial','seconds':1,'audit':{'reviews':[{'reason':'unclear'}],
        'pages':[{'trace':[{'error':'same'}]}],'page_results':[{'reason':None}]}},
        {'status':'accepted','seconds':1,'audit':{'pages':[{'trace':[{'error':'same'}]}]}}]
    summary=aggregate(rows)
    assert summary['accepted']==1
    assert summary['partial']==1
    assert summary['review_tasks']==1
    assert summary['repeated_rejections']==0
