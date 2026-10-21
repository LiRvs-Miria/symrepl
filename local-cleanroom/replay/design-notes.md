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
> oracle，代码不入境（只进网的 oracle 臂）。本册与 [`../server/design-notes.md`](../server/design-notes.md)
> 平行；总进度=[`../../local-cleanroom-progress.md`](../../local-cleanroom-progress.md)。

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
