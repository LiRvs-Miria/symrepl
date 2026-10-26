# replay design-notes — 引擎线（回放核）设计档案

## E2 BranchSite 值代数与路径对比（2026-10-21，纯件；契约定稿+落地）

**三答定稿（用户，模糊架构级，2026-10-21）**：
① BranchSite 形状：**x86 专用**（aarch64 不做）；结构 =（address, function, mnemonic）
——size/target 不属站点本体；加法代数测到达；路径点 = BranchSite 的 list，
路径相符（重合）= 逐元素比较。
② 对比代数：逐元素比较的结果 = 代数等价运算的结果，取值无非**相等/不等/错误**
——照家法 Expect/Maybe 错误代数（E1 KtestBad 同构：封闭 reason + detail 当值不抛）。
③ 网钉范围（2026-10-21 定稿）：**无 oracle 对账**——用户裁"我们是要取代原始架构
和代码，没必要使用原来的"。M1 branches.py/trace.py 连 oracle 臂也不入；
E2 正确性=纯形状钉 + 后续 E4/branchTrace 真会话网兜底（多网交叉：本刀管形状，
行为后验）。渲染面不入本刀，随横向 branchTrace 砖。

**转录后果（照①②推定，用户复核）**：
- 结构无 kind 字段 ⇒ 识别=纯助记符表（x86 单表，小写化）；M1 的
  GetControlFlowKind 权威路拿掉（x86-only 下两路在本机永不分歧）
- target 出局 ⇒ `parse_branch_target`/operand 抽取不入境；预扫描纯面=is_conditional
  单件（x86 表）
- size 不存储 ⇒ 指令长在用到处供给（站点值不携带）

**定形史（2026-10-21，三轮撞裁，原话在案）**：
- **第一轮（步 2，两钉）**：+ 吐 int 被打回（"你现在吐出int有点离谱"）→
  `+ : BranchSite -> int -> BranchSite`；step 混杂形 (site,size,next_addr,run_ended)
  被打回 → `==(observed, target)` 双站点。
- **第二轮（步 3，三钉，函数式定形）**：①**纯值不许混方法**——`__add__` 入值
  类型=OOP 杂质（"如果你觉得不能做成代数结构,你应该直接提出来"）；代数运算一律
  住模块函数。②**数据可得性模型**：target 侧三字段全知；replay 侧
  address/function 取自调用栈、mnemonic 取自反汇编（LLDB `SBInstruction.GetMnemonic`
  可得——用户存疑已答，取值在 E4 壳侧，纯代数不问来源）。③**== 自下而上**：
  字段各自 ==（int/str 原生）→ 单站点 == = 逐域合取（`equal` 显式铸名）→
  路径对比 = `map` 高阶逐位；**实际执行只负责往实际 list 记录站点，对比是独立
  代数**（领域分层锚：记录在壳，对比在纯核）。
- **随动收敛**：Step/StepResult 枚举退役（元素结果=Bool）；UNOBSERVED 退役
  （无到达测量即无末点不可观测）→ `PathBad(LENGTH_MISMATCH)`（长度不齐=逐位
  对比无定义，detail 携两侧长度）；裸停址序列、+ 到达代数出局（现文本用不到，
  shell 预扫描若需再铸）。

**铸名（撞到随铸，可复议）**：`equal`（单站点 ==）、`match_path`（路径对比）、
`PathBad/PathBadReason(LENGTH_MISMATCH)`、`MatchOutcome = list[bool] | PathBad`。

**落地记录（2026-10-21 当日）**：RED 12（模块缺位全红基线）→步 1（ADT+识别表）
4 绿→步 2（+代数/走向三员）5 绿→第一轮两钉打回→改形新钉 RED 7（精准红）→
12/12→**第二轮三钉打回（函数式定形）**→新钉 RED 8（纯值/equal/match_path 两参/
PathBadReason，识别四查不动仍绿）→重转录 **12/12 ALL PASS**。**网钉自愈一枚**：
纯值查 `hasattr("__add__")` 撞 tuple 继承面（NamedTuple 原生拼接语义混淆）——
修=类自身 `__dict__` + 行为面 `site+int` 必炸 TypeError 双钉。**纯网四件回归绿**
（probe_ktest/test_core/framing/childseam）；server/ 零 diff，LLDB 六网无风险。
**网搬运**：from-import 全有全无 → 模块属性访问，小步部分绿可见、缺名不连坐
（E1 家法微进化）。攻击点了结：归一化/aarch64 拒收/无地址捷径（逐域合取钉死）/
长度不齐当值/空输入，五枚全由网收口。

**不变量**：零代码继承（本刀 M1 代码完全不入境——取代裁决，E1 的 oracle 臂模式
对本刀不适用）；**BranchSite 纯值无方法**；== 逐域合取无地址捷径；纯核零 I/O；
比较域不抛（错误当值）；注释只说代码本身（裁决史归本档）。

**网（probe_branches.py，纯网不起服不占端口）**：形状钉=值 ADT（三字段+纯值面）/
识别表三查/站点 == 两查/整路 map 逐位（同值/逐位/长度不齐/空输入）/错误代数封闭；
RED 预期=模块缺位全红、改形处精准红（清单即需求文本）。

---

> 新册（2026-10-20，用户裁"新册"，册名 `replay/` 与 M1 包名对齐——转录前撞裁，可复议）：
> M1 回放核（`src/symrepl/replay/`，ktest/branches/trace/driver 共 1007 行）的 cleanroom
> 重写。性质=承重件（安全×程序分析交叠域核心因果链）。**零代码继承**：M1=规格源+行为
> oracle，代码不入境（只进网的 oracle 臂）。本册与 [`server.md`](server.md)
> 平行；总进度=[`../../progress.md`](../../progress.md)。

## E1 ktest 解析重写（2026-10-20，纯读；契约）

**四答定稿（用户，模糊架构级）**：①纯读即可（无 dump——M1 dump 为 harness 重投喂设，
DAP 面只消费 ktest 路径）②Expect 代数包结果（KTest 值或解析异常，当值返回不抛）
③错误前置照家法先例（framing FrameBad 同构：reason 封闭枚举+detail 串）④新册。

**字节布局（规格冻结，源=ADR-0002 + KLEE lib/Basic/KTest.cpp 2026-10-03 核对；
全整数 uint32 大端）**：

```
magic      : 5 字节——b"KTEST"（现行）或 b"BOUT\n"（legacy），读侧双收
version    : uint32 BE；>3 拒（新格式永不误析）；==1 无 sym 域；>=2 携 sym 两域
numArgs    : uint32 BE
args       : numArgs × string（uint32 BE 长度 + 裸字节）
[v>=2]     : symArgvs uint32 BE, symArgvLen uint32 BE
numObjects : uint32 BE
objects    : numObjects × { name: string; numBytes: uint32 BE; data: numBytes 字节 }
```

**值 ADT（字段同款 M1，NamedTuple 家法）**：
- `KTestObject(name: str, data: bytes)`
- `KTest(version: int, args: tuple[str, ...], sym_argvs: int, sym_argv_len: int,
  objects: tuple[KTestObject, ...])`——v1 无 sym 域报告 0/0（M1 同款，oracle 对齐优先，
  缺席建模不作）

**Expect 代数（错误前置）**：
- `KtestBadReason(Enum, auto)`：`BAD_MAGIC`（首五字节双魔都不中）/ `VERSION_TOO_NEW`
  （version>3）/ `TRUNCATED`（严格短读）——M1 三 raise 位一一对应，封闭枚举
  （新败类=新成员，detail 禁兼职分类）
- `KtestBad(reason, detail)`（NamedTuple；detail 携 offset/need/have 等人读事实）
- `KtestOutcome = KTest | KtestBad`
- `parse(data: bytes) -> KtestOutcome`（纯核：不抛、不猜、不续）
- `load(path) -> KtestOutcome`（壳：open/read 转交 parse；OS 错误照 M1 传播——壳路
  I/O 不入代数）

**行为规格钉（全部 M1 对齐，diff 网裁决）**：
- 截断：严格短读（need>remaining 即败）；海量 numObjects/超大 numBytes 必须快败
  （不挂、不爆内存）
- 尾随字节容忍（KLEE 不读，remaining 不查）；零尺寸对象合法；version 0 合法（仅 >3 拒）
- 名/串解码 utf-8/surrogateescape（任意字节不炸）

**网（probe_ktest.py，纯网不起服不占端口）**：
- **oracle 对账**：真 ktest 六件（get_sign/two_objs 各三）+合成字节案例喂双解析器；
  成臂逐字段 diff（M1 dataclass ↔ cleanroom NamedTuple 按名取），败臂 M1 异常消息
  前缀→reason 对位（oracle 定义败类，cleanroom 认类）
- 值形状钉：tuple 面/v1 sym 0-0/零尺寸/非 UTF-8 名/枚举成员可点
- load：真路径=parse(bytes) 同果；缺文件 FileNotFoundError 传播
- RED 预期：模块缺位=全查 FAIL（纯件新册无桩可对——RED 清单即需求文本）

**不变量**：零代码继承（M1 ktest.py 只活在网的 oracle 臂）；纯核零 I/O（load 除外，
壳）；parse 域不抛；reason 封闭；字段名与 M1 全同（oracle diff 按名取）。

**预埋攻击点**：①version 0 边界（仅 >3 拒——别想当然拒 0）②海量 numObjects/numBytes
快败（take 先于 range 消耗炸）③surrogateescape 译名与 M1 逐字节对齐 ④尾随字节不读
（numObjects 耗尽即停，remaining 不查）⑤NamedTuple 与 M1 dataclass 字段名一致
（args/sym_argvs/sym_argv_len/objects）⑥BOUT\n 魔与 KTEST 魔双收但**写侧无份**（纯读）。

**落地记录（2026-10-20 当日）**：RED 10（模块缺位全红基线）→转录一次过→**10/10 ALL PASS，零网炸**。**撞裁一枚（错误交付形状）**：M1 `_Reader.take` raise KTestError 的 fail-fast 形状 1:1 保留——私有 `_Truncated` 控制流+parse 边界 try/except **单点**转 KtestBad（调用方契约"不抛"不破）；逐点 None 检查案（take→bytes|None）不取：六处调用点漏检一处=裸 None 传染进 unpack 炸 TypeError，漏检面是隐性 bug、单点转值是显性结构。**语料册自检前置兑现**：17 合成例+6 真件先对 M1 oracle 合预期（ALL ALIGNED）才进转录——自检挡掉语料册自身两处 layout 错（BOUT/v3 合成例漏 sym 域读成截断、装饰器语法缺函数体），语料错与转录错就此可分责。**网况**：probe_ktest 10/10+纯网三件回归绿（test_core/framing/childseam）；LLDB 六网零风险（server/ 零 diff，6d5d1bb 后未动）。攻击点了结：①version 0 收（v0 例双解析器同果）②海量声明快败（两例即断，无挂起）③非 UTF-8 名与 M1 同译（surrogateescape 逐字节对齐）④尾随字节不读（同值 diff）⑤字段名全同（按名 diff 过）⑥纯读无写侧（BOUT 魔仅读收）。

---

## M4-a 差分回放矩阵（2026-10-26，契约；ADR-0006）

**三答定稿（用户，模糊架构级，2026-10-26）**：①扰动=**位翻转基线**（归因到
bit；尺寸分档降采样；超限降级「未分析」非静默截断）②走向 v1=**命中集差分**
（per 变体 `(function,line)` 命中集+exit_code；成员跨变体变化⇒input-dependent；
ADR-0004 裁决 5 不翻，server/ 零 diff）③**ktest 写侧准入**（显式翻 E1 四答
定稿①；`dump` 纯函数；单魔 KTEST；往返钉 `parse(dump(k)) == k`）。

**立档**：ADR-0006（差分矩阵=第二支柱污点叠加地基，与 ADR-0005 一致性支柱
分档；离线工具位，回放环之外）。

**件结构（家法：纯值无方法、模块函数、错误当值、纯核零 I/O）**：
- 核件 `src/replay/diffmat.py`（新册件；铸名撞到随铸可复议）：
  - `flip(k, obj, byte, bit) -> KTest | FlipBad`——单位翻转；三向越界当值
  - `variants(k, cap) -> VariantPlan`——位翻转全列；分档降采样**确定性**
    （同输入同计划，无随机）；cap 超限如实标未分析档
  - `RunObserved(hit_set, exit_code)`；`matrix(runs) -> SiteMatrix`——
    站点→走向集合
  - `dependent(sm) -> 站点级判定+置信级别`——走向集合>1⇒input-dependent；
    恒定只标「本观测内未见依赖」（翻转=必要非充分）；置信 full/capped
    如实标
  - `crosscheck(fork_set, analysis) -> dict[Site, Arm]`——fork∧flip=强断言
    （STRONG）/ concrete∧flip=传播边界报警（PROPAGATION_BOUNDARY，pathmark
    运行时自检镜）/ fork∧恒定=覆盖不足（INSUFFICIENT_COVERAGE）/
    concrete∧恒定=QUIET（双侧无信号第四格，2×2 全空间恰一臂；三 informative
    臂+QUIET，2026-10-26 步 5 撞裁）；fork_set=pathmark.fork_sites() 直通
    （ForkSite 行级键与 Site 元组同构）
- 壳件：`ktest.dump(KTest) -> bytes`（入 ktest.py，写侧）；驱动壳独立件
  ——per 变体子进程照 lldb_backend 家法（`make_backend → launch(变体,
  scope) → continue_/poll_events → trace_report` 收命中集+exit_code）；
  `_created` 一进程一 Session 守门遵循；落位转录时裁

**补裁（2026-10-26，用户）**：**IR 精确化=可选参数**（ADR-0006 裁决 8）。
无 `.ll` 时差分/判定纯动态自成报；提供则 `crosscheck` 入链（互证三臂）。
照 launch `ir` 参数家法：缺席零增。报告如实标互证缺席。

**补裁二（2026-10-26，用户）**：**恒被求值站点如实标盲**（ADR-0006 裁决
9）。首 if 类站点（get_sign L11）每条路径都执行，命中集差分结构性失明——
判定维持 v1 如实标（未见依赖；互证在场走覆盖不足臂），不在站点级摊程序级
证据。**验收读法随之修正**：「三站点」=三出口（exit 0/1/255）全可由翻转
可达；fork 站点 L13 标 input-dependent，L11 走盲区臂。

**报告形状（2026-10-26 补裁定形；纯值 Report + text 渲染壳，trace_report
{text,nodes} 家法）**：
- 头：baseline ktest／靶／scope／变体数／cap／分档／置信 full|capped／
  互证在场否
- 站点行：`(function,line)` ×走向集合（hit/miss 计数，baseline 入册标
  run0）×判定（input-dependent｜本观测内未见依赖）×驱动翻转（位级
  [(obj,byte,bit)]）×互证臂（强断言｜传播边界报警｜覆盖不足｜缺席）
- 尾：exit 分布×驱动位；程序级附注（exit 随翻转变化⇒输出 input-dependent，
  证据类型=程序级，不摊到站点）；未分析档清单

**报告样张（推演，手算自钉死真值——基线字节 probe_ktest REAL_PINS／exit 表
M3 验收／fork 集 pathmark 真值；非机跑）**：

```
# M4-a 差分回放矩阵 — get_sign
baseline : get_sign_test000001.ktest (a=00 00 00 00)   exit 0   hits {11}
variants : 32（a 4B×8 位翻转全列）+ baseline(run0) = 33 runs    置信: full
互证     : 在场（get_sign.ll → fork={(get_sign,11),(get_sign,13)}）

site (get_sign, 11)   x==0 分支
  走向集合 : hit 33/33（恒定）
  判定     : 本观测内未见依赖            ← 恒被求值站点，命中集差分盲（补裁二）
  互证     : fork ∧ 恒定 ⇒ 覆盖不足

site (get_sign, 13)   x<0 分支
  走向集合 : miss 1（run0）/ hit 32
  判定     : input-dependent
  驱动翻转 : 32/32 全部位（任一位翻转即破 x==0，L13 随即可达）
  互证     : fork ∧ flip ⇒ 强断言

exit 分布 : 0 × 1（run0）｜1 × 31（翻成任意正数）｜255 × 1（a[3]bit7 → INT_MIN）
程序级    : exit 随翻转变化 ⇒ 输出 input-dependent；255 臂驱动位=a[3]bit7（位级定位）
```

**落地记录**：步 1 `dump`（2026-10-26 当日）——转录一次过；probe_ktest
**13/13 ALL PASS**（D1-D3 绿，读侧 10 查零回归）＋强证：六真件
`dump(parse(x)) == x` **逐字节对表全中**（与 KLEE 3.1 序列化布局一致）；
pytest 20/20、probe_branches rc=0、test_diffmat 维持 RED 9（步 2/3 面）。
步 2 `flip`（当日）——转录一次过；**B1-B5 绿**（单位翻转/纯值不共享/
三向越界当值/对合/零尺寸出界）；网微进化一枚=E2 先例复用：from-import
全有全无改**模块属性访问**（variants 缺名不连坐 flip，V 面 RED 精准
`AttributeError: no attribute 'variants'`）；pytest 20/20、probe_ktest
13/13、probe_branches rc=0。攻击点了结：三向越界当值（B3）＋负数入
range 拒（`0 <=` 显式）＋零尺寸对象出界（B5）——对合律 B4 顺带钉。
步 3 `variants`（当日）——转录一次过；**V1-V4 绿，test_diffmat 9/9
ALL PASS**。撞到即裁一枚=采样形状：cap 超限走**全域均匀 stride**（含首尾、
严格递增下标、无随机；大对象密度按比例占优=「按尺寸分档」的 v1 语义），
per-object 显式分档押后（报告步见覆盖偏斜再裁）；`unanalyzed`=全位−入选
（capped 如实标）；负 cap 按 0。**内部不变量显性化**（E1 家法）：variants
内 flip 位姿恒在界内，FlipBad 臂不可达→AssertionError 显式钉而非静默窄化。
回归 pytest 20/20、probe_ktest 13/13。
步 4 `matrix`/`dependent`（当日）——**先 RED 后转录**：M 面 4 查钉入
（判定双向样张同形/恒定不谎/置信如实+capped 只动标签/空输入边缘）→
RED 4 精准（`no attribute 'RunObserved'/'matrix'`）→转录一次过→
**test_diffmat 13/13 ALL PASS**。铸名撞到随铸：`Site`（=pathmark
ForkSite 行级键同构）、`SiteMatrix`（dict 别名，站点→成员状态集）、
`Verdict`/`Analysis`（capped 由调用方传旗——置信是计划属性非矩阵属性，
撞裁在案）。exit_code 随行不参与判定（v1 语义；攻击点④留报告步）。
步 5 `crosscheck`（当日）——X 面 5 查 RED 先钉→转录一次过→**test_diffmat
18/18 ALL PASS**。撞裁一枚=**第四格 QUIET**：2×2 互证空间 concrete∧恒定
（双侧无信号）三臂无名字——钉 `Arm.QUIET` 如实第四格（非 informative 臂，
ADR"三臂"指三 informative 臂文本不破），契约件结构行同步；签名细化
`crosscheck(fork_set, analysis)`（站点全集=verdicts 键，dependent_set 推导
集会丢 QUIET 格）。接面钉 X5：pathmark.ForkSite 行级键同构直通零换算。
回归 pytest 20/20、test_pathmark 0 fail、probe_ktest 13/13。
步 6a 归因+报告（当日）——`VariantPlan` 增 `positions` 对齐字段（V5）+
`driving_positions` 位级归因+`exit_classes`+`render` 报告渲染（R1-R4，含
全文钉一张：**RED 钉失手一枚系手算错非转录错**——fork 传参与 hit/miss
计数两处，render 无改照跑）；23/23 ALL PASS。
步 6b 真饵网（当日）——`probe_m4a.py` 直驱 lldb_backend（一变体一子进程，
probe_e3 家法；**网炸一枚自愈**：插桩站点命中后 inferior 停住无人放行——
DAP 链客户端职责在直驱模式由驱动壳自担，每 stopped 批次续跑一次）。
**#19 验收三件全过**：get_sign 33 runs（G1-G6）——三出口全可达、255 唯
驱动位=(0,3,7) 位级定位、L13 强断言 32/32、L11 盲区如实+覆盖不足臂，
实跑报告与推演样张逐行同形；two_objs 49 runs（T1-T4）——mismatch 双对象
独立触发（exit-1 驱动位=arr[0]×8+b[1]×8 恰横跨两对象）。**真发现一枚**
（挂账候选）：`two_objs.c:11` 的 `&&` 组合（IR 层 `and i1`）在 pathmark
轻量闭包外→(main,11) 落噪声B，互证 QUIET 而非覆盖不足——M4-a 镜像照出
闭包传播边界（and/or 组合是否入闭包=承重裁决留用户）；exit 驱动位照实
定位双对象（程序级归因补位站点级盲区，两支柱互补活证）。
**终版证据**：test_diffmat 23/23+probe_m4a 11/11+全网 sweep 20 网 rc=0×20
（含 probe_ir/probe_chaos 等真饵网）+pytest 20/20；server/ 零 diff
（M1+M3 回归不破）。待提交停机（提交门等用户逐笔示下）。
回归 pytest 20/20、probe_ktest 13/13。

**不变量**：纯核零 I/O；错误当值（Expect 家法）；纯值无方法；server/ 零
diff；置信不谎（恒定≠input-independent，capped 如实标）；降采样确定性。

**预埋攻击点**：①dump 往返对称（surrogateescape 名/串原样回写；v1 无 sym
域不写；BOUT 读件写回 KTEST 单魔）②flip 越界（obj/byte/bit 三向）当值
③降采样确定性 ④命中集同而 exit 异的变体算什么证据，撞到裁 ⑤互证三臂
覆盖全空间 ⑥驱动壳一进程一 Session（`_created` 家法）。

**网**：纯件网 `test_diffmat`（flip 代数/variants 分档与 cap/矩阵判定/互证
三臂，ADT 直喂零工具链依赖）；dump 往返钉就近挂 `probe_ktest`（D 系列）；
真饵网 `probe_m4a`（真 LLDB+真靶，随驱动壳步落地）。回归面：probe_ir/
test_pathmark/probe_ktest/test_core/pytest 全绿，server/ 零 diff。

**验收（#19 原文，2026-10-26 用户确认读法）**：get_sign 站点集 {11,13}
（x==0/x<0）×三基线 exit {0,1,255} 全部可由翻转可达且定位驱动字节；
two_objs mismatch 分支两对象翻转均可触发；M1+M3 形态回归不破。
