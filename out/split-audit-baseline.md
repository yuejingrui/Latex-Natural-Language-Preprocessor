# 分割审计基线：无句末标点的句子

由 `split_audit.py` 生成，未手改。分类规则见脚本；`other` 类需要人工看。

## 汇总

| 项 | 值 |
|:--|--:|
| 论文 | 21 |
| 句子 | 13300 |
| 无句末标点 | 324 |
| 标点在公式里（良性） | 31 |
| 标点在括号内（良性） | 22 |
| 容器边界：列表条目/逗号结尾（设计取舍） | 137 |
| 标题：证明/步骤等（非句子） | 9 |
| 句末是 display、正文无标点 | 72 |
| 句末是 inline 公式、正文无标点 | 12 |
| 裸花括号残留（缺陷） | 14 |
| 疑似被切断（缺陷） | 0 |
| 原文缺句末标点（需复核） | 27 |
| 其它（需人工） | 0 |

## 逐篇

| 论文 | 句子 | 无句末标点 |
|:--|--:|--:|
| 0708.2512 | 785 | 9 |
| 1504.04132 | 227 | 5 |
| 1512.07573 | 893 | 24 |
| 1607.08880 | 392 | 15 |
| 1702.02560 | 64 | 7 |
| 1708.05648 | 577 | 24 |
| 1802.02089 | 639 | 1 |
| 1808.01468 | 951 | 16 |
| 1901.04848 | 2055 | 92 |
| 1906.11723 | 120 | 1 |
| 1912.05938 | 1009 | 7 |
| 2006.04872 | 648 | 26 |
| 2011.07716 | 347 | 10 |
| 2105.02998 | 454 | 5 |
| 2111.12660 | 797 | 18 |
| 2206.12386 | 342 | 4 |
| 2301.01196 | 429 | 17 |
| 2307.14871 | 207 | 0 |
| 2402.19174 | 595 | 22 |
| 2410.11622 | 420 | 4 |
| danmark | 1349 | 17 |

## 标点在公式里（良性）（31）

- `0708.2512` s103 — [2.] For any MATH236, the following properties (REF70), (REF71) and (REF72) hold for the sets MATH237
- `1504.04132` s167 — Therefore, by the Fefferman-Stein vector-valued inequality (see REF66, see also REF67) we obtain MATH329
- `1607.08880` s95 — It is proved in REF49 (p.39) that for every MATH176 the above numbers satisfy the equalities MATH177
- `1607.08880` s97 — Under this assumption the result in REF51 (p.39) also implies that MATH180
- `1702.02560` s40 — Using REF13 we get MATH123
- `1702.02560` s58 — For each MATH176 we have MATH177 and MATH178 and thus MATH179
- `1708.05648` s38 — Define MATH81
- `1708.05648` s109 — By averaging (REF89) over the ball MATH210, MATH211
- `1708.05648` s337 — Given a map MATH715, and MATH716 and MATH717 we define the MATH718-th MATH719-stratification MATH720 by MATH721
- `1708.05648` s368 — We integrate the above and by the fact that MATH780 and for MATH781, MATH782 we have MATH783
- `1808.01468` s123 — Moreover, in this case, MATH528
- `1808.01468` s906 — By direct calculation, we obtain MATH2521 where for MATH2522 and MATH2523, we have MATH2524
- `1901.04848` s103 — We denote the associated spherical reflection by MATH222; it is defined by MATH223
- `1901.04848` s236 — For MATH615 such that MATH616, we set MATH617
- `1901.04848` s252 — Then MATH646
- `1901.04848` s254 — As MATH653, we get MATH654
- `1901.04848` s412 — Since MATH1079 has signature MATH1080, MATH1081
- `1901.04848` s414 — We write MATH1084 with MATH1085, and from MATH1086, we get that MATH1087
- `1901.04848` s775 — We define MATH2018 by MATH2019
- `1901.04848` s1891 — By REF679, MATH5199 gives rise to a morphism MATH5200
- `2006.04872` s242 — It is the boundary of an immersed pair of pants MATH323 coming from an immersion MATH324
- `2011.07716` s119 — Equivalently, when does MATH361 vanish under the map MATH362
- `2105.02998` s142 — where MATH276
- `2206.12386` s214 — Then: (i): for every MATH836, MATH837; (ii): there is a minimizer MATH838 of MATH839; (iii): if MATH840 is a minimizer of MATH841 with MATH842, then MATH843, MA
- `2206.12386` s250 — We apply this with MATH1002 given by MATH1003
- `2402.19174` s523 — We have MATH1372
- `danmark` s192 — In general, one has tautological implications MATH315 MATH316
- `danmark` s195 — As such, we have MATH320
- `danmark` s910 — Proof of Proposition REF257
- `danmark` s973 — Proof of Proposition REF277
- `danmark` s1238 — Proof of Proposition REF337

## 标点在括号内（良性）（22）

- `0708.2512` s118 — (For more detail, see Remark REF83 and Section REF84.)
- `1512.07573` s647 — (This is a general fact about opposites of twisted arrow categories.)
- `1512.07573` s723 — (Hence the ordinary Segal condition for a simplicial space MATH1051 is the case where MATH1052 is a point.)
- `1512.07573` s845 — (Similarly, since epimorphisms have uniquely determined kernels, the whole diagram can also be reconstructed from the last column.)
- `1607.08880` s169 — (In particular MATH358 on MATH359.)
- `1708.05648` s256 — By the monotonicity formula for MATH548 we have MATH549 {is uniformly bounded.}
- `1808.01468` s393 — {Connected components of MATH1337.}
- `1808.01468` s583 — {Proof of Theorem REF305: part 1.}
- `1808.01468` s635 — {First step in the proof of Theorem REF332.}
- `1808.01468` s698 — {String property for Demazure-like subsets of MATH2103.}
- `1808.01468` s794 — {Proof of Theorem REF422: part 2.} {Recursion formula for the right-hand side of REF423.}
- `1808.01468` s832 — {Second step in the proof of Theorem REF447.}
- `1808.01468` s850 — {Proof of Corollary REF463.}
- `1808.01468` s900 — *{Appendix.} {Examples of formula REF495.}
- `1808.01468` s911 — {Right-hand side of REF501 in the case that MATH2540 is not dominant.}
- `2011.07716` s289 — (The MATH925 in the exponent ensures this is independent of MATH926 by the product formula and the MATH927 ensures this is independent of MATH928.)
- `2402.19174` s151 — {Theorem REF69 in the special case that MATH357 has just two columns was obtained in REF70 using different techniques.}
- `2402.19174` s436 — Let MATH1112 denote the set of MATH1113-tableaux, where MATH1114 is a MATH1115-partition.}
- `2402.19174` s443 — We call these cosets MATH1135-tabloids.}
- `2410.11622` s51 — (I.e.MATH74 is a real form of MATH75.)
- `2410.11622` s264 — Plugging this in, we get MATH757 (The first formula is more suited for computations, the second for theoretical purposes.)
- `2410.11622` s362 — The notation is quite symbolic, suppressing the weight factors MATH1012 and the measures for the integrations over MATH1013.)

## 容器边界：列表条目/逗号结尾（设计取舍）（137）

- `0708.2512` s114 — Then
- `0708.2512` s253 — Then
- `0708.2512` s433 — Then
- `0708.2512` s535 — Then
- `1512.07573` s552 — For a simplicial space MATH719, the following are equivalent
- `1512.07573` s553 — [1.] MATH720 is a decomposition space
- `1512.07573` s833 — A gap complex in an abelian category MATH1230 is a functor MATH1231 such that
- `1607.08880` s40 — A Landau-Ginzburg model is a pair MATH44, where
- `1607.08880` s45 — A tame compactified Landau-Ginzburg model is the data MATH49, where
- `1607.08880` s47 — [2.] MATH52 is a reduced normal crossings divisor such that
- `1607.08880` s304 — There are the isomorphisms
- `1702.02560` s9 — Assume MATH23, MATH24, and MATH25 are as in the Buchsbaum-Eisenbud-Horrocks Conjecture, and that in addition
- `1702.02560` s10 — [1.] MATH26 is locally a complete intersection and MATH27 is MATH28-torsion free, or
- `1702.02560` s17 — If either
- `1702.02560` s18 — [1.] MATH48 is the quotient of a regular local ring by a regular sequence of elements and MATH49 is invertible in MATH50, or
- `1702.02560` s19 — [2.] MATH51 contains MATH52 as a subring for an odd prime MATH53,
- `1708.05648` s104 — Then
- `1708.05648` s222 — Then there exists a subsequence of MATH455 (which we still denote by MATH456) such that
- `1708.05648` s329 — Given a map MATH683, we say that
- `1708.05648` s427 — Then for every MATH899, there exist MATH900 such that if for some MATH901 with MATH902 in MATH903 and MATH904 we have
- `1708.05648` s428 — [a.] MATH905 MATH906-effectively span a MATH907-dimensional subspace MATH908,
- `1708.05648` s429 — [a.] MATH909 for all MATH910,
- `1708.05648` s447 — For MATH937 there exists MATH938 such that if MATH939 and there exists MATH940 in MATH941 such that
- `1708.05648` s480 — Moreover for each MATH1010 one of the following is satisfied
- `1901.04848` s68 — If either
- `1901.04848` s69 — [1.] MATH172 is odd, or
- `1901.04848` s70 — [2.] MATH173 is even, MATH174, and MATH175,
- `1901.04848` s118 — A Bridgeland stability condition on MATH253 is a pair MATH254, where
- `1901.04848` s119 — the central charge MATH255 is a group homomorphism, and
- `1901.04848` s120 — MATH256 is a slicing of MATH257,
- `1901.04848` s148 — Moreover,
- `1901.04848` s201 — MATH526 for MATH527,
- `1901.04848` s223 — Then for MATH582 such that MATH583, MATH584 if and only if
- `1901.04848` s224 — [1.] MATH585 and MATH586 or
- `1901.04848` s225 — [2.] MATH587 and MATH588 or
- `1901.04848` s226 — [3.] MATH589, MATH590, and MATH591 or
- `1901.04848` s237 — Then
- `1901.04848` s240 — [3.] MATH625 is normal, unless
- `1901.04848` s241 — [(a)] MATH626 with MATH627 and MATH628, or
- `1901.04848` s298 — It is sufficient to show that
- `1901.04848` s299 — [1.] the natural morphism MATH763 is representable by schemes, and
- `1901.04848` s394 — [3.] There are infinitely many effective spherical or exceptional classes in MATH1022, and either
- `1901.04848` s395 — [(a)] there exist exactly two MATH1023-stable spherical objects MATH1024 whose classes are in MATH1025; or
- `1901.04848` s396 — [(b)] there exist exactly four MATH1026-stable exceptional objects MATH1027 with MATH1028; or
- `1901.04848` s429 — [(TSS2)] there exists an isotropic class MATH1127 with MATH1128 and MATH1129; or
- `1901.04848` s430 — [(TSS3)] there exists a primitive isotropic class MATH1130 such that MATH1131 and MATH1132 for a spherical MATH1133; or
- `1901.04848` s520 — [(1)] If MATH1408, then let MATH1409 be the torsion pair of MATH1410 such that
- `1901.04848` s525 — [(2)] If MATH1422, then let MATH1423 be the torsion pair of MATH1424 such that
- `1901.04848` s532 — Then MATH1437 is MATH1438-semistable if and only if
- `1901.04848` s533 — [(a)] MATH1439 and MATH1440 is MATH1441-semistable or
- `1901.04848` s536 — Then MATH1446 is MATH1447-semistable if and only if
- `1901.04848` s537 — [(a)] MATH1448 and MATH1449 is MATH1450-semistable or
- `1901.04848` s605 — [(1)] Let MATH1632 be the torsion pair of MATH1633 such that
- `1901.04848` s606 — [(a)] MATH1634 is the subcategory of MATH1635 generated by MATH1636-stable objects MATH1637 with MATH1638 and
- `1901.04848` s609 — [(2)] Let MATH1645 be the torsion pair of MATH1646 such that
- `1901.04848` s683 — [(1)] Let MATH1814 be the torsion pair of MATH1815 such that
- `1901.04848` s686 — [(2)] Let MATH1822 be a torsion pair of MATH1823 such that
- `1901.04848` s913 — In this case, MATH2412 for each MATH2413 and a generic MATH2414; or
- `1901.04848` s917 — Finally, in this case MATH2421 for a generic MATH2422, while MATH2423 is a totally semistable wall for MATH2424; or
- `1901.04848` s968 — [1.] If MATH2552 is totally semistable, that is, MATH2553, then
- `1901.04848` s969 — [(a)] MATH2554 contains an effective exceptional or spherical class MATH2555 such that MATH2556; or
- `1901.04848` s970 — [(b)] MATH2557 contains a primitive isotropic MATH2558 such that MATH2559 and MATH2560; or
- `1901.04848` s971 — [(c)] MATH2561 contains a primitive isotropic MATH2562 and an effective spherical class MATH2563 such that MATH2564, MATH2565, MATH2566, where MATH2567 is a nod
- `1901.04848` s972 — [(d)] MATH2568 contains a primitive isotropic MATH2569 and an effective exceptional class MATH2570 such that MATH2571, MATH2572, and MATH2573; or
- `1901.04848` s974 — [2.] MATH2581 induces a divisorial contraction if and only if
- `1901.04848` s975 — [(a)] MATH2582 contains a primitive isotropic MATH2583 and an effective exceptional class MATH2584 such that MATH2585, MATH2586, and MATH2587; or
- `1901.04848` s976 — [(b)] MATH2588 contains a primitive isotropic MATH2589 and an effective spherical class MATH2590 such that MATH2591 and MATH2592; or
- `1901.04848` s977 — [(c)] MATH2593 contains a primitive isotropic MATH2594 and an effective exceptional or spherical class MATH2595 such that MATH2596 and MATH2597; or
- `1901.04848` s1010 — [1.] If MATH2694 is totally semistable for MATH2695, then either
- `1901.04848` s1011 — [(a)] MATH2696 for primitive isotropic MATH2697 with MATH2698; or
- `1901.04848` s1013 — [2.] If MATH2704, then either
- `1901.04848` s1014 — [(a)] MATH2705 for primitive isotropic MATH2706; or
- `1901.04848` s1015 — [(b)] MATH2707 for the unique spherical or exceptional class MATH2708; or
- `1901.04848` s1178 — [2.] If MATH3146, then
- `1901.04848` s1179 — [(a)] MATH3147 for a primitive isotropic MATH3148, or
- `1901.04848` s1252 — [2.] If either
- `1901.04848` s1253 — [(a)] MATH3369 or
- `1901.04848` s1301 — [1.] If MATH3524 and either
- `1901.04848` s1302 — [(a)] MATH3525, or
- `1901.04848` s1304 — [2.] If either
- `1901.04848` s1305 — [(a)] MATH3530, MATH3531, MATH3532, and MATH3533, or
- `1901.04848` s1336 — MATH3646; or
- `1901.04848` s1358 — If either
- `1901.04848` s1359 — [1.] MATH3696 and MATH3697 can be written as a sum MATH3698 with MATH3699 such that MATH3700 if for each MATH3701, MATH3702 and MATH3703; or
- `1901.04848` s1360 — [2.] there exists an exceptional class MATH3704 and either
- `1901.04848` s1361 — [(a)] MATH3705, or
- `1901.04848` s1362 — [(b)] MATH3706 and MATH3707; or
- `1901.04848` s1363 — [3.] there exists a spherical class MATH3708 and either
- `1901.04848` s1364 — [(a)] MATH3709, or
- `1901.04848` s1365 — [(b)] MATH3710 and MATH3711 is a spherical class,
- `1901.04848` s1501 — There is an object MATH4093 which induces a Fourier-Mukai transform MATH4094 such that
- `1901.04848` s1524 — Then there is an autoequivalence MATH4151 such that
- `1901.04848` s1707 — MATH4662 and MATH4663, or
- `1901.04848` s1720 — Moreover, if
- `1901.04848` s1721 — [1.] MATH4708 for a spherical or exceptional class MATH4709, or
- `1901.04848` s1722 — [2.] MATH4710 for an isotropic vector MATH4711, or
- `1901.04848` s1723 — [3.] MATH4712 for an isotropic vector MATH4713 with MATH4714,
- `1901.04848` s1780 — For a non-trivial extension MATH4879,
- `1901.04848` s1808 — MATH4960 with MATH4961, or
- `1901.04848` s1977 — Then there is a Mukai vector MATH5431 and an elliptic fibration MATH5432 such that
- `1912.05938` s617 — A BPS structure consists of
- `1912.05938` s676 — Then
- `1912.05938` s778 — Then the heart of a bounded t-structure on MATH1660 is a full additive subcategory MATH1661 such that
- `1912.05938` s828 — Then
- `1912.05938` s903 — Then
- `2006.04872` s147 — The first natural collar of a boundary component is
- `2006.04872` s148 — the open horoball with boundary length MATH171 around a cusp boundary element,
- `2006.04872` s149 — the set of points at distance strictly less than MATH172 from a boundary cone point of angle MATH173,
- `2006.04872` s160 — The MATH181-th natural collar MATH182 of a boundary component MATH183 is
- `2006.04872` s161 — an open horoball of boundary length MATH184 around if MATH185 is a cusp,
- `2006.04872` s162 — the set of points at distance strictly less than MATH186 if MATH187 is a cone point of angle MATH188,
- `2011.07716` s280 — [1.] MATH888 is an isometry for every MATH889,
- `2011.07716` s281 — [2.] MATH890 is continuous for any open subset MATH891 of MATH892 and MATH893,
- `2105.02998` s139 — There exists a curve MATH270 such that
- `2105.02998` s140 — [1.] MATH271 admits MATH272 points specializing to all of the MATH273, and
- `2111.12660` s110 — Then there is a sequence MATH224 of Hölder probability measures and a sequence MATH225 of closed subsets of MATH226 such that
- `2111.12660` s112 — [2.] For every MATH231 and nonzero integer polynomial MATH232, MATH233; and
- `2111.12660` s151 — Given a positive integer MATH338 and nonzero integer polynomials MATH339, we define MATH340 to be the unique integer polynomial MATH341 of maximal degree so tha
- `2111.12660` s152 — The leading term of MATH342 is positive,
- `2111.12660` s153 — The greatest common denominator of the coefficients of MATH343 is MATH344 (that is, MATH345 is primitive), and
- `2111.12660` s348 — Then there is a real monic polynomial MATH863 of degree MATH864 such that
- `2111.12660` s349 — [1.] MATH865 is squarefree and has all roots in MATH866,
- `2111.12660` s350 — [2.] We have MATH867,
- `2111.12660` s351 — [3.] For any integer MATH868 in the interval MATH869, the degree MATH870 coefficient of MATH871 is an even integer, and
- `2111.12660` s503 — With MATH1294 and MATH1295 fixed as above, and given an integer MATH1296, there is MATH1297 so we have the following: For every integer MATH1298, there is a mon
- `2301.01196` s95 — MATH61 is nonsingular
- `2301.01196` s97 — MATH64 has only l.c.i singularities
- `2301.01196` s100 — An MATH68-marked stable quasimap of genus MATH69 and degree MATH70 to MATH71 is MATH72 where
- `2301.01196` s363 — Define the moduli space of relative quasimaps MATH580 to be the locus of absolute quasimaps MATH581 in MATH582 such that for every MATH583, a connected componen
- `2402.19174` s156 — Then MATH367 is an irreducible in MATH368 if and only if MATH369 has the form MATH370, where
- `2402.19174` s261 — for some MATH677, the bracketed permutations MATH678 and MATH679 have more than one letter or
- `2402.19174` s268 — Then
- `2402.19174` s269 — [1.] the multiplicity of MATH692 in MATH693 is 1
- `2402.19174` s291 — Indeed, in any bracketed permutation MATH738 of MATH739, there are three kinds of MATH740-brackets MATH741, namely, those in which
- `2402.19174` s353 — Now by Theorem REF115 we have,
- `2402.19174` s433 — A MATH1105-tableau MATH1106 is a filling of the nodes of MATH1107 that satisfies
- `danmark` s773 — We get a functor MATH1422,

## 标题：证明/步骤等（非句子）（9）

- `1708.05648` s39 — We have
- `1808.01468` s728 — \bqed
- `1808.01468` s744 — \bqed
- `1808.01468` s751 — \bqed
- `2006.04872` s492 — Orthogeodesics between geodesic boundaries
- `2006.04872` s534 — Orthogeodesics between cone points
- `2006.04872` s569 — Cusps and infinite grades
- `2301.01196` s94 — We insist that
- `2402.19174` s594 — Acknowledgements

## 句末是 display、正文无标点（72）

- `1504.04132` s40 — A good counterexample illustrating this is a sequence MATH72 defined by MATH73
- `1504.04132` s74 — To see this it suffices to consider MATH123
- `1504.04132` s149 — Next, for any MATH289 and MATH290 we have MATH291
- `1512.07573` s23 — An admissible cut MATH15 partitions the nodes of MATH16 into two subsets or `layers' MATH17
- `1512.07573` s341 — The amalgamated ordinal sum over MATH370 of two objects MATH371 and MATH372, denoted MATH373, is given by the pushout of inert maps MATH374
- `1512.07573` s344 — Every active map with source MATH380 splits as MATH381
- `1512.07573` s346 — With notation as above, the active-inert factorisation of the composite of an inert map MATH382 followed by an active map MATH383 is given by MATH384
- `1512.07573` s605 — There is a canonical equivalence of monoidal categories (an isomorphism, if we consider the usual skeleta of these categories) MATH809
- `1512.07573` s635 — Given MATH898, property (b) says that for any inert map MATH899 we may define MATH900
- `1512.07573` s645 — Finally we observe that this diagram, with MATH934 and MATH935, also serves to show that the construction of MATH936 from MATH937 is well defined on MATH938
- `1512.07573` s728 — For MATH1068 we can factor the square as MATH1069
- `1512.07573` s776 — As an example, both groupoids MATH1118 and MATH1119 are equivalent to the groupoid of diagrams MATH1120
- `1512.07573` s846 — We have MATH1253
- `1607.08880` s150 — For the pair MATH301 we have the corresponding long exact homology sequence MATH302
- `1607.08880` s184 — The inclusion of the pairs MATH388 induces a morphism of the homology sequences MATH389
- `1607.08880` s200 — [(ii)] Consider the commutative diagram MATH428
- `1607.08880` s252 — For MATH528 by Lemma REF88(i) the induced long exact sequence of cohomology MATH529 looks like MATH530
- `1607.08880` s253 — For MATH531 by Lemma REF89(ii) the same long exact sequence looks like MATH532
- `1607.08880` s257 — By Poincare duality for MATH538 one has MATH539
- `1607.08880` s259 — Consider the long exact sequence of homology MATH540
- `1708.05648` s51 — We also have MATH113
- `1708.05648` s52 — As a corollary MATH114
- `1708.05648` s80 — MATH147 is divergence free MATH148
- `1708.05648` s141 — Then MATH276
- `1708.05648` s339 — Using this fact we define the MATH724th MATH725-stratification by MATH726
- `1802.02089` s347 — Using REF211 and REF212 in case MATH802 and MATH803, we compute the derivative MATH804
- `1901.04848` s777 — Then MATH2022
- `1901.04848` s1534 — Then we have a commutative diagram MATH4182
- `1901.04848` s1600 — We have an isomorphism MATH4366 with a commutative diagram MATH4367
- `2006.04872` s54 — Any MATH46 satisfies MATH47
- `2006.04872` s249 — This allows us to define a partial order on MATH343 given by MATH344
- `2006.04872` s298 — We define MATH427 to be the geodesic in the homotopy class MATH428 and MATH429
- `2006.04872` s501 — Using standard trigonometry identities, we have MATH926
- `2006.04872` s511 — Using the quadrilateral formula along with the summation formula for MATH949 after some simplification we obtain MATH950
- `2006.04872` s512 — Eliminating MATH951 in these two equations and rearranging MATH952
- `2006.04872` s522 — Solving this equation we obtain MATH973
- `2006.04872` s525 — [1.] For finite MATH981 and MATH982, the gap measure is MATH983
- `2006.04872` s527 — [3.] If MATH986 then MATH987
- `2011.07716` s21 — Recall an element MATH56 is self-dual if for all MATH57 we have MATH58
- `2011.07716` s313 — For the third condition we will construct a generating set of global sections MATH990 of MATH991 such that MATH992
- `2111.12660` s52 — For any complex number MATH82, take MATH83 to be the Borel measure defined by MATH84
- `2111.12660` s117 — Given a complex polynomial MATH243 and a nonnegative integer MATH244, we then define the MATH245-norm of MATH246 with respect to MATH247 by MATH248
- `2111.12660` s136 — Then MATH294
- `2111.12660` s170 — Given integers MATH391, the determinantal definition of the discriminant also gives us MATH392
- `2111.12660` s323 — From REF145 and REF146, we then have MATH800
- `2111.12660` s431 — Take MATH1089
- `2111.12660` s537 — We then take MATH1383
- `2111.12660` s568 — [2.] For all MATH1439, we have MATH1440
- `2206.12386` s92 — Moreover, letting MATH364 denote the inverse of MATH365, we have MATH366
- `2301.01196` s51 — At least in genus zero, the proposed picture is MATH44
- `2301.01196` s255 — Therefore we have a diagram MATH359
- `2301.01196` s287 — Recall from REF102 and REF103 that MATH412 extends to a morphism MATH413 if and only if MATH414 is MATH415 where MATH416 is defined by MATH417
- `2301.01196` s312 — The associated (dual) distinguished triangle is MATH463
- `2301.01196` s329 — Using the criterion of REF116, the complex MATH500, which is cohomologically supported in MATH501, is of perfect amplitude contained in MATH502 if and only if f
- `2301.01196` s346 — If MATH534 is a subset of the toric boundary corresponding to rays MATH535 (set MATH536), then there is a logarithmic Euler sequence MATH537
- `2301.01196` s348 — When MATH539 is toric the morphism MATH540 becomes very explicit, given by the following diagram MATH541
- `2301.01196` s351 — Consequently, by forming the mapping cone (and shifting) we have that MATH552 is given by the three term complex MATH553
- `2301.01196` s352 — But the right-hand map is now surjective so this complex is quasi-isomorphic to a two term complex MATH554
- `2301.01196` s413 — Next, consider the diagram MATH684
- `2301.01196` s421 — Consider the tower of cartesian diagrams MATH697
- `2402.19174` s109 — Then the eigenvalues of MATH242 are nonnegative and MATH243
- `2402.19174` s534 — Clearly MATH1414
- `2402.19174` s545 — For the second case, we have MATH1437
- `2410.11622` s309 — Define MATH848
- `danmark` s372 — By Lemma REF88, the composition MATH649 naturally factors as MATH650
- `danmark` s571 — MATH1043, i.e., by pull-push along the diagram MATH1044
- `danmark` s658 — These naturally upgrade to a map MATH1241 over MATH1242, giving rise to the correspondence MATH1243
- `danmark` s671 — For each MATH1260, we have a correspondence MATH1261
- `danmark` s869 — The above functor factors as MATH1606
- `danmark` s1142 — Form the Cartesian diagram MATH2110
- `danmark` s1152 — Form the Cartesian diagram MATH2128
- `danmark` s1296 — Instead, Gaitsgory defines a prestack MATH2396, which we recall below, that does fit into a Cartesian diagram MATH2397

## 句末是 inline 公式、正文无标点（12）

- `0708.2512` s588 — Note that Propositions REF377 and REF378 are valid for non-maximal MATH1648
- `1708.05648` s94 — Concerning the error term MATH180, we have and MATH181 satisfies the following properies on MATH182
- `1708.05648` s432 — See also Proposition 4.6 and its proof in REF237
- `1912.05938` s391 — We will write MATH752 for the set of all isomorphism classes of rigidified framed MATH753-local systems on MATH754
- `2105.02998` s141 — [2.] The geometric fundamental group of MATH274 surjects onto MATH275
- `2301.01196` s96 — MATH62 acts freely on MATH63
- `2402.19174` s157 — [1.] MATH371 is an irreducible of MATH372, MATH373
- `2402.19174` s238 — Indeed, the column tabloids MATH605, where MATH606 is a semistandard MATH607-tableau of shape MATH608, form a basis for MATH609 and the column tabloids MATH610,
- `2402.19174` s292 — [1.] only one of MATH742 and MATH743 are single letters in MATH744
- `2402.19174` s293 — [2.] both MATH745 and MATH746 are single letters in MATH747
- `2402.19174` s434 — each node MATH1108 is filled with a column MATH1109 of distinct positive integers of length MATH1110
- `2402.19174` s435 — the sets of entries of the columns are disjoint and their union is MATH1111

## 裸花括号残留（缺陷）（14）

- `1504.04132` s1 — \selectlanguage{english}
- `1512.07573` s40 — {.5 } *{Background and motivation}
- `1512.07573` s54 — {.5 } *{Abstraction steps: from numbers to sets, and from sets to MATH33-groupoids}
- `1512.07573` s86 — {.5 } *{The idea of decomposition spaces}
- `1512.07573` s108 — {.5 } *{Related work: MATH82-Segal spaces of Dyckerhoff and Kapranov}
- `1512.07573` s116 — {.5 } *{Related work on Möbius categories}
- `1512.07573` s126 — {.5 } *{Outline of the present paper, section by section}
- `1512.07573` s157 — {.5 } *{Brief summary of the four sequels to this paper}
- `1512.07573` s192 — {.5 } *{Acknowledgments}
- `1808.01468` s904 — { 0.1in MATH2518}
- `2011.07716` s19 — {.3 } {Self-dual elements}
- `2011.07716` s113 — {.3 } {Descent for MATH346-line bundles}
- `2011.07716` s159 — {.3 } {Quotient of the regular representation}
- `2011.07716` s195 — {.3 } {MATH626 determines an immersion of MATH627}

## 疑似被切断（缺陷）（0）

（无）

## 原文缺句末标点（需复核）（27）

- `0708.2512` s352 — The assertion (REF225) in Main Theorem REF226 holds
- `0708.2512` s361 — Since MATH966, we have MATH967, which completes the proof of the proposition
- `1607.08880` s69 — Recall that this data defines a canonical (monodromy) weight filtration centered at MATH110, MATH111 of MATH112 MATH113 with the properties
- `1708.05648` s438 — The following theorem says that MATH922 is almost constant on the pinched points
- `1708.05648` s496 — Now we cover MATH1055 by balls MATH1056 such that
- `1708.05648` s548 — At the step MATH1158 of our induction we have
- `1808.01468` s928 — By the same argument as for MATH2578 in §REF510, we see that
- `1901.04848` s158 — For MATH367, define MATH368, and consider the heart MATH369 defined by MATH370, where MATH371 and MATH372 are defined by
- `1901.04848` s199 — Thus MATH520, where MATH521 is the open subscheme of the Quot scheme parametrizing quotients MATH522 such that
- `1901.04848` s1335 — As the birational map in REF482 is isomorphic in codimension one unless
- `1901.04848` s1706 — When MATH4659 is primitive and MATH4660 is defined by an isotropic vector MATH4661 with
- `1901.04848` s1807 — Indeed, by REF654 for MATH4956 generic with respect to MATH4957 satisfying MATH4958, MATH4959 is normal unless
- `1906.11723` s120 — Department of Mathematics, University of Thessaly 3rd km Old National Road Lamia – Athens, 35100, Lamia, Greece E-mail address: ppolymerakis@uth.gr
- `1912.05938` s661 — To any generic GMN differential MATH1382, we associate the triple MATH1383 where
- `2006.04872` s1 — Prime orthogeodesics, concave cores and families of identities on hyperbolic surfaces
- `2006.04872` s7 — Key words and phrases: Identities, trace equalities, moduli spaces of hyperbolic surfaces, orthogeodesics, concave cores, immersed pairs of pants
- `2006.04872` s513 — For MATH953 (that is, MATH954) we solve for MATH955 in equation (REF44) to get the two solutions
- `2006.04872` s515 — Letting MATH959 and MATH960 go towards infinity
- `2006.04872` s540 — Orthogeodesics from a cone point to a geodesic
- `2006.04872` s547 — Orthogeodesics from a geodesic to a cone point
- `2006.04872` s648 — Addresses: A. B.: The Graduate Center, CUNY, 365 Fifth Ave., N.Y., N.Y., 10016 and Hunter College, CUNY, 695 Park Ave., N.Y., N.Y., 10065, USA H.P.: Department 
- `2105.02998` s389 — Following Theorem REF67 we construct an irreducible curve MATH1000 such that
- `2206.12386` s10 — The following list of results, summarized in
- `2402.19174` s260 — We say that a bracketed permutation MATH676 is a non-comb if either
- `danmark` s615 — Indeed, denote by MATH1135 the prestack parametrizing triples MATH1136, where
- `danmark` s817 — Consider the algebraic stack MATH1500 classifying triples MATH1501, where
- `danmark` s879 — Indeed, it follows by REF238 that the Verma module MATH1621 is in the image of (REF239)

## 其它（需人工）（0）

（无）
