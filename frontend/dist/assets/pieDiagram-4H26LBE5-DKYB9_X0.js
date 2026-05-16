import{M as q,aB as J,N as K,aC as Q,R as Y,aE as ee,a as s,ah as w,P as te,m as ae,aA as ie,ao as re,A as B,an as se,x as ne,n as oe,B as le,H as ce}from"./mermaid-vendor-OxmAju9m.js";import{p as de}from"./chunk-4BX2VUAB-BgLfuMi4.js";import{p as pe}from"./wardley-L42UT6IY-CwCw_Dg9.js";import"./markdown-vendor-BU83kELG.js";var ge=ce.pie,C={sections:new Map,showData:!1},u=C.sections,D=C.showData,he=structuredClone(ge),ue=s(()=>structuredClone(he),"getConfig"),fe=s(()=>{u=new Map,D=C.showData,oe()},"clear"),me=s(({label:e,value:a})=>{if(a<0)throw new Error(`"${e}" has invalid value: ${a}. Negative values are not allowed in pie charts. All slice values must be >= 0.`);u.has(e)||(u.set(e,a),w.debug(`added new section: ${e}, with value: ${a}`))},"addSection"),ve=s(()=>u,"getSections"),xe=s(e=>{D=e},"setShowData"),Se=s(()=>D,"getShowData"),F={getConfig:ue,clear:fe,setDiagramTitle:ee,getDiagramTitle:Y,setAccTitle:Q,getAccTitle:K,setAccDescription:J,getAccDescription:q,addSection:me,getSections:ve,setShowData:xe,getShowData:Se},we=s((e,a)=>{de(e,a),a.setShowData(e.showData),e.sections.map(a.addSection)},"populateDb"),Ce={parse:s(async e=>{const a=await pe("pie",e);w.debug(a),we(a,F)},"parse")},De=s(e=>`
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
`,"getStyles"),$e=De,ye=s(e=>{const a=[...e.values()].reduce((r,o)=>r+o,0),$=[...e.entries()].map(([r,o])=>({label:r,value:o})).filter(r=>r.value/a*100>=1);return le().value(r=>r.value).sort(null)($)},"createPieArcs"),Te=s((e,a,$,y)=>{w.debug(`rendering pie chart
`+e);const r=y.db,o=te(),T=ae(r.getConfig(),o.pie),A=40,n=18,p=4,c=450,d=c,f=ie(a),l=f.append("g");l.attr("transform","translate("+d/2+","+c/2+")");const{themeVariables:i}=o;let[E]=re(i.pieOuterStrokeWidth);E??=2;const b=T.textPosition,g=Math.min(d,c)/2-A,G=B().innerRadius(0).outerRadius(g),L=B().innerRadius(g*b).outerRadius(g*b);l.append("circle").attr("cx",0).attr("cy",0).attr("r",g+E/2).attr("class","pieOuterCircle");const h=r.getSections(),P=ye(h),N=[i.pie1,i.pie2,i.pie3,i.pie4,i.pie5,i.pie6,i.pie7,i.pie8,i.pie9,i.pie10,i.pie11,i.pie12];let m=0;h.forEach(t=>{m+=t});const _=P.filter(t=>(t.data.value/m*100).toFixed(0)!=="0"),v=se(N).domain([...h.keys()]);l.selectAll("mySlices").data(_).enter().append("path").attr("d",G).attr("fill",t=>v(t.data.label)).attr("class","pieCircle"),l.selectAll("mySlices").data(_).enter().append("text").text(t=>(t.data.value/m*100).toFixed(0)+"%").attr("transform",t=>"translate("+L.centroid(t)+")").style("text-anchor","middle").attr("class","slice");const O=l.append("text").text(r.getDiagramTitle()).attr("x",0).attr("y",-400/2).attr("class","pieTitleText"),k=[...h.entries()].map(([t,S])=>({label:t,value:S})),x=l.selectAll(".legend").data(k).enter().append("g").attr("class","legend").attr("transform",(t,S)=>{const z=n+p,X=z*k.length/2,Z=12*n,j=S*z-X;return"translate("+Z+","+j+")"});x.append("rect").attr("width",n).attr("height",n).style("fill",t=>v(t.label)).style("stroke",t=>v(t.label)),x.append("text").attr("x",n+p).attr("y",n-p).text(t=>r.getShowData()?`${t.label} [${t.value}]`:t.label);const I=Math.max(...x.selectAll("text").nodes().map(t=>t?.getBoundingClientRect().width??0)),U=d+A+n+p+I,R=O.node()?.getBoundingClientRect().width??0,H=d/2-R/2,V=d/2+R/2,M=Math.min(0,H),W=Math.max(U,V)-M;f.attr("viewBox",`${M} 0 ${W} ${c}`),ne(f,c,W,T.useMaxWidth)},"draw"),Ae={draw:Te},Me={parser:Ce,db:F,renderer:Ae,styles:$e};export{Me as diagram};
