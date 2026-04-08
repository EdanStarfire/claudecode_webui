import{g as j,s as H,a as J,b as Q,q as Y,p as ee,_ as s,l as w,c as te,F as ae,I as ie,K as re,L as z,M as se,e as ne,y as oe,N as le,G as ce}from"./mermaid-vendor-5h7YZYHR.js";import{p as de}from"./chunk-4BX2VUAB-CQ_3T01K.js";import{p as pe}from"./wardley-RL74JXVD-CxTs7Ph9.js";import"./markdown-vendor--pE7db_I.js";import"./min-b_RXMkXE.js";import"./_baseUniq-C5T2rNoi.js";var ge=ce.pie,C={sections:new Map,showData:!1},u=C.sections,D=C.showData,he=structuredClone(ge),ue=s(()=>structuredClone(he),"getConfig"),fe=s(()=>{u=new Map,D=C.showData,oe()},"clear"),me=s(({label:e,value:a})=>{if(a<0)throw new Error(`"${e}" has invalid value: ${a}. Negative values are not allowed in pie charts. All slice values must be >= 0.`);u.has(e)||(u.set(e,a),w.debug(`added new section: ${e}, with value: ${a}`))},"addSection"),ve=s(()=>u,"getSections"),xe=s(e=>{D=e},"setShowData"),Se=s(()=>D,"getShowData"),G={getConfig:ue,clear:fe,setDiagramTitle:ee,getDiagramTitle:Y,setAccTitle:Q,getAccTitle:J,setAccDescription:H,getAccDescription:j,addSection:me,getSections:ve,setShowData:xe,getShowData:Se},we=s((e,a)=>{de(e,a),a.setShowData(e.showData),e.sections.map(a.addSection)},"populateDb"),Ce={parse:s(async e=>{const a=await pe("pie",e);w.debug(a),we(a,G)},"parse")},De=s(e=>`
  .pieCircle{
    stroke: ${e.pieStrokeColor};
    stroke-width : ${e.pieStrokeWidth};
    opacity : ${e.pieOpacity};
  }
  .pieOuterCircle{
    stroke: ${e.pieOuterStrokeColor};
    stroke-width: ${e.pieOuterStrokeWidth};
    fill: none;
  }
  .pieTitleText {
    text-anchor: middle;
    font-size: ${e.pieTitleTextSize};
    fill: ${e.pieTitleTextColor};
    font-family: ${e.fontFamily};
  }
  .slice {
    font-family: ${e.fontFamily};
    fill: ${e.pieSectionTextColor};
    font-size:${e.pieSectionTextSize};
    // fill: white;
  }
  .legend text {
    fill: ${e.pieLegendTextColor};
    font-family: ${e.fontFamily};
    font-size: ${e.pieLegendTextSize};
  }
`,"getStyles"),ye=De,$e=s(e=>{const a=[...e.values()].reduce((r,o)=>r+o,0),y=[...e.entries()].map(([r,o])=>({label:r,value:o})).filter(r=>r.value/a*100>=1);return le().value(r=>r.value).sort(null)(y)},"createPieArcs"),Te=s((e,a,y,$)=>{w.debug(`rendering pie chart
`+e);const r=$.db,o=te(),T=ae(r.getConfig(),o.pie),A=40,n=18,p=4,c=450,d=c,f=ie(a),l=f.append("g");l.attr("transform","translate("+d/2+","+c/2+")");const{themeVariables:i}=o;let[b]=re(i.pieOuterStrokeWidth);b??=2;const _=T.textPosition,g=Math.min(d,c)/2-A,L=z().innerRadius(0).outerRadius(g),B=z().innerRadius(g*_).outerRadius(g*_);l.append("circle").attr("cx",0).attr("cy",0).attr("r",g+b/2).attr("class","pieOuterCircle");const h=r.getSections(),I=$e(h),N=[i.pie1,i.pie2,i.pie3,i.pie4,i.pie5,i.pie6,i.pie7,i.pie8,i.pie9,i.pie10,i.pie11,i.pie12];let m=0;h.forEach(t=>{m+=t});const E=I.filter(t=>(t.data.value/m*100).toFixed(0)!=="0"),v=se(N).domain([...h.keys()]);l.selectAll("mySlices").data(E).enter().append("path").attr("d",L).attr("fill",t=>v(t.data.label)).attr("class","pieCircle"),l.selectAll("mySlices").data(E).enter().append("text").text(t=>(t.data.value/m*100).toFixed(0)+"%").attr("transform",t=>"translate("+B.centroid(t)+")").style("text-anchor","middle").attr("class","slice");const O=l.append("text").text(r.getDiagramTitle()).attr("x",0).attr("y",-400/2).attr("class","pieTitleText"),k=[...h.entries()].map(([t,S])=>({label:t,value:S})),x=l.selectAll(".legend").data(k).enter().append("g").attr("class","legend").attr("transform",(t,S)=>{const W=n+p,V=W*k.length/2,X=12*n,Z=S*W-V;return"translate("+X+","+Z+")"});x.append("rect").attr("width",n).attr("height",n).style("fill",t=>v(t.label)).style("stroke",t=>v(t.label)),x.append("text").attr("x",n+p).attr("y",n-p).text(t=>r.getShowData()?`${t.label} [${t.value}]`:t.label);const P=Math.max(...x.selectAll("text").nodes().map(t=>t?.getBoundingClientRect().width??0)),U=d+A+n+p+P,R=O.node()?.getBoundingClientRect().width??0,q=d/2-R/2,K=d/2+R/2,F=Math.min(0,q),M=Math.max(U,K)-F;f.attr("viewBox",`${F} 0 ${M} ${c}`),ne(f,c,M,T.useMaxWidth)},"draw"),Ae={draw:Te},We={parser:Ce,db:G,renderer:Ae,styles:ye};export{We as diagram};
