import{a as b,m,aB as $,x as C,ah as w,aD as S,N as D,aF as T,R as F,M as P,aC as z,O as E,H as A,n as W}from"./mermaid-vendor-Bbjrzh__.js";import{p as _}from"./chunk-4BX2VUAB-C8rDNSYe.js";import{p as N}from"./wardley-L42UT6IY-DrPb1FHK.js";var M=A.packet,u,v=(u=class{constructor(){this.packet=[],this.setAccTitle=S,this.getAccTitle=D,this.setDiagramTitle=T,this.getDiagramTitle=F,this.getAccDescription=P,this.setAccDescription=z}getConfig(){const t=m({...M,...E().packet});return t.showBits&&(t.paddingY+=10),t}getPacket(){return this.packet}pushWord(t){t.length>0&&this.packet.push(t)}clear(){W(),this.packet=[]}},b(u,"PacketDB"),u),L=1e4,O=b((e,t)=>{_(e,t);let r=-1,s=[],n=1;const{bitsPerRow:l}=t.getConfig();for(let{start:a,end:i,bits:d,label:c}of e.blocks){if(a!==void 0&&i!==void 0&&i<a)throw new Error(`Packet block ${a} - ${i} is invalid. End must be greater than start.`);if(a??=r+1,a!==r+1)throw new Error(`Packet block ${a} - ${i??a} is not contiguous. It should start from ${r+1}.`);if(d===0)throw new Error(`Packet block ${a} is invalid. Cannot have a zero bit field.`);for(i??=a+(d??1)-1,d??=i-a+1,r=i,w.debug(`Packet block ${a} - ${r} with label ${c}`);s.length<=l+1&&t.getPacket().length<L;){const[p,o]=Y({start:a,end:i,bits:d,label:c},n,l);if(s.push(p),p.end+1===n*l&&(t.pushWord(s),s=[],n++),!o)break;({start:a,end:i,bits:d,label:c}=o)}}t.pushWord(s)},"populate"),Y=b((e,t,r)=>{if(e.start===void 0)throw new Error("start should have been set during first phase");if(e.end===void 0)throw new Error("end should have been set during first phase");if(e.start>e.end)throw new Error(`Block start ${e.start} is greater than block end ${e.end}.`);if(e.end+1<=t*r)return[e,void 0];const s=t*r-1,n=t*r;return[{start:e.start,end:s,label:e.label,bits:s-e.start},{start:n,end:e.end,label:e.label,bits:e.end-n}]},"getNextFittingBlock"),x={parser:{yy:void 0},parse:b(async e=>{const t=await N("packet",e),r=x.parser?.yy;if(!(r instanceof v))throw new Error("parser.parser?.yy was not a PacketDB. This is due to a bug within Mermaid, please report this issue at https://github.com/mermaid-js/mermaid/issues.");w.debug(t),O(t,r)},"parse")},H=b((e,t,r,s)=>{const n=s.db,l=n.getConfig(),{rowHeight:a,paddingY:i,bitWidth:d,bitsPerRow:c}=l,p=n.getPacket(),o=n.getDiagramTitle(),g=a+i,h=g*(p.length+1)-(o?0:a),k=d*c+2,f=$(t);f.attr("viewBox",`0 0 ${k} ${h}`),C(f,h,k,l.useMaxWidth);for(const[y,B]of p.entries())I(f,B,y,l);f.append("text").text(o).attr("x",k/2).attr("y",h-g/2).attr("dominant-baseline","middle").attr("text-anchor","middle").attr("class","packetTitle")},"draw"),I=b((e,t,r,{rowHeight:s,paddingX:n,paddingY:l,bitWidth:a,bitsPerRow:i,showBits:d})=>{const c=e.append("g"),p=r*(s+l)+l;for(const o of t){const g=o.start%i*a+1,h=(o.end-o.start+1)*a-n;if(c.append("rect").attr("x",g).attr("y",p).attr("width",h).attr("height",s).attr("class","packetBlock"),c.append("text").attr("x",g+h/2).attr("y",p+s/2).attr("class","packetLabel").attr("dominant-baseline","middle").attr("text-anchor","middle").text(o.label),!d)continue;const k=o.end===o.start,f=p-2;c.append("text").attr("x",g+(k?h/2:0)).attr("y",f).attr("class","packetByte start").attr("dominant-baseline","auto").attr("text-anchor",k?"middle":"start").text(o.start),k||c.append("text").attr("x",g+h).attr("y",f).attr("class","packetByte end").attr("dominant-baseline","auto").attr("text-anchor","end").text(o.end)}},"drawWord"),R={draw:H},j={byteFontSize:"10px",startByteColor:"black",endByteColor:"black",labelColor:"black",labelFontSize:"12px",titleColor:"black",titleFontSize:"14px",blockStrokeColor:"black",blockStrokeWidth:"1",blockFillColor:"#efefef"},G=b(({packet:e}={})=>{const t=m(j,e);return`
	.packetByte {
		font-size: ${t.byteFontSize};
	}
	.packetByte.start {
		fill: ${t.startByteColor};
	}
	.packetByte.end {
		fill: ${t.endByteColor};
	}
	.packetLabel {
		fill: ${t.labelColor};
		font-size: ${t.labelFontSize};
	}
	.packetTitle {
		fill: ${t.titleColor};
		font-size: ${t.titleFontSize};
	}
	.packetBlock {
		stroke: ${t.blockStrokeColor};
		stroke-width: ${t.blockStrokeWidth};
		fill: ${t.blockFillColor};
	}
	`},"styles"),q={parser:x,get db(){return new v},renderer:R,styles:G};export{q as diagram};
