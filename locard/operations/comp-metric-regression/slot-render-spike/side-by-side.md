# Slot-render vs composed — published metrics (isolated spike)

Composed = what the LLM wrote (current). Slot = rendered from value+label (proposed).
Same grounded value in both. `thin` = label too weak to stand alone.

| id | composed (current) | slot-rendered (proposed) | compact | thin |
|---|---|---|---|---|
| 752d2b78:1 | ACTS helped 3,375 individuals through its programs in 2022. | 3,375 individuals helped | 3.4K individuals helpe |  |
| 752d2b78:2 | ACTS served 1,328 households in 2022. | 1,328 households served | 1.3K households served |  |
| 752d2b78:4 | Over 11% of individuals and families in the region are being evicted s | 11% eviction rate since COVID | 11% |  |
| 752d2b78:5 | Rents are up 11.5% over 2021 in the Greater Richmond region. | 11.5% rent increase | 11.5% |  |
| 752d2b78:6 | 58% of all renters in the region are worried about becoming homeless. | 58% renters worried about homelessness | 58% |  |
| 752d2b78:7 | 90,438 calls for assistance were made into the Homeless Connection Lin | 90,438 HCL calls for assistance | 90.4K HCL calls for as |  |
| 752d2b78:8 | ACTS provided 1,953 unique referrals to community resources in 2022. | 1,953 unique referrals | 2K unique referrals |  |
| 072b705a:0 | Fred Finch Youth Center opened Rising Oaks, a 30-unit transitional hou | 30 transitional housing units opened | 30 transitional housin |  |
| 072b705a:2 | 85% of the first 30 tenants who moved into Rising Oaks continued to ca | 85% tenant retention rate | 85% |  |
| 072b705a:3 | The school-linked Wellness Center on the Oakland Campus is scheduled t | 1,200 annual medical visits planned | 1.2K annual medical vi |  |
| 072b705a:4 | The Wellness Center project is primarily funded by a $500,000 grant fr | $500,000 HRSA grant amount | 500K USD |  |
| 072b705a:5 | In the first six months of fiscal year 2014, FFYC experienced a 145% g | 145% fundraising revenue growth | 145% |  |
| 072b705a:6 | Total assets for Fred Finch Youth Center in 2013 were $18,613,555. | $18,613,555 total assets | 18.6M USD |  |
| 072b705a:7 | Total net assets in 2013 were $7,017,208. | $7,017,208 total net assets | 7M USD |  |
| 81265722:2 | Youth who co-created community support plans with mentors showed highe | 80% community support plan completion rate | 80% |  |
| 81265722:3 | Programs that combined job readiness training with mentorship and supp | 74% school or work re-engagement rate | 74% |  |
| 81265722:7 | The evaluation included 16 stakeholder interviews and 3 youth focus gr | 52 evaluation participants | 52 evaluation particip |  |
| 5b98b66f:0 | Capstone served 3,256 Vermonters through its food shelf. | 3,256 food shelf recipients | 3.3K food shelf recipi |  |
| 5b98b66f:1 | Capstone provided heating assistance to 2,971 beneficiaries. | 2,971 heating assistance beneficiaries | 3K heating assistance  |  |
| 5b98b66f:2 | Capstone helped 1,562 children access healthy meals and snacks. | 1,562 children receiving meals | 1.6K children receivin |  |
| 5b98b66f:3 | Capstone weatherized 181 homes. | 181 homes weatherized | 181 homes weatherized |  |
| 5b98b66f:7 | Capstone had 32 graduates of its Community Kitchen Academy. | 32 CKA graduates | 32 CKA graduates |  |
| 5b98b66f:8 | Capstone secured $1,903,873 in tax refunds for low-income households t | $1,903,873 tax refunds secured | 1.9M USD |  |
| 5b98b66f:10 | Capstone's total revenue in 2019 was $15,333,465. | $15,333,465 total revenue | 15.3M USD |  |
| 5b98b66f:11 | Capstone's total expenses in 2019 were $15,079,384. | $15,079,384 total expenses | 15.1M USD |  |
| f92248ef:0 | Allen Neighborhood Center served 331 neighbors through health coverage | 331 neighbors enrolled in services | 331 neighbors enrolled |  |
| f92248ef:1 | The Allen Farmers Market drew 18,000 patrons in 2021. | 18,000 farmers market patrons | 18K farmers market pat |  |
| f92248ef:3 | 206 volunteers contributed 5,886 hours of service in 2021. | 5,886 volunteer hours | 5.9K volunteer hours |  |
| f92248ef:4 | The Food Hub has generated $830,553 in gross sales since 2015, with 85 | $830,553 food hub gross sales since 2015 | 830.6K USD |  |
| f92248ef:5 | 69 food entrepreneurs have started their businesses in the ANC Kitchen | 69 food entrepreneurs started | 69 food entrepreneurs  |  |
| f92248ef:6 | 128 Market Walk participants walked 2,644 miles during summer 2021. | 2,644 miles walked by participants | 2.6K miles walked by p |  |
| f92248ef:8 | The Rathbun Accelerator launched with 4 food entrepreneurs sharing res | 4 accelerator entrepreneurs | 4 accelerator entrepre |  |
| c09c96c2:1 | Total revenues for fiscal year 2006 were $346,657. | $346,657 total revenues | 346.7K USD |  |
| c09c96c2:2 | Total expenses for fiscal year 2006 were $255,363. | $255,363 total expenses | 255.4K USD |  |
| c09c96c2:3 | The organization ended the year with a small deficit of $1,594. | $1,594 net loss | 1.6K USD |  |
| c09c96c2:4 | Membership grew to over 635 members in 2006. | 635 members | 635 members |  |
| c09c96c2:5 | Membership appeal raised $46,020 in contributions. | $46,020 membership revenue | 46K USD |  |
| c09c96c2:6 | The Harvest Dinner and Raffle netted over $19,000. | $19,000 harvest dinner net revenue | 19K USD |  |
| c09c96c2:7 | The Golf Tournament raised over $20,436 despite a postponement due to  | $20,436 golf tournament net revenue | 20.4K USD |  |
| c09c96c2:8 | Ski Bradford netted $11,275 for Community Service, exceeding budget by | $11,275 ski bradford net revenue | 11.3K USD |  |
| c09c96c2:9 | Breakfast with Santa attracted nearly 500 guests. | 500 breakfast with santa attendance | 500 breakfast with san |  |
| c09c96c2:10 | Three $1,000 Theresa Hearne Scholarships were awarded to graduating se | $3 scholarships awarded | 3 USD |  |
| c09c96c2:11 | The organization's total assets were $1,266,416 as of December 31, 200 | $1,266,416 total assets | 1.3M USD |  |
| e6fb382a:0 | Good Shepherd Community Care served more than 1500 donors in the progr | 1,500 total donors | 1.5K total donors |  |
| e6fb382a:1 | Good Shepherd costs Medicare about 45% less per patient admitted than  | 45% cost savings vs for-profit | 45% |  |
| e6fb382a:2 | Nonprofit hospices like Good Shepherd provide about 20% more services  | 20% more services per patient | 20% |  |
| e6fb382a:3 | Good Shepherd's operating revenues and support totaled $8,142,116. | $8,142,116 operating revenues | 8.1M USD |  |
| e6fb382a:4 | Good Shepherd's operating expenses were $8,488,379. | $8,488,379 operating expenses | 8.5M USD |  |
| e6fb382a:5 | Good Shepherd's change in net assets from operations was a loss of $34 | $-346,263 operating net change | -346.3K USD |  |
| e6fb382a:6 | Good Shepherd's total net assets were $5,807,656. | $5,807,656 total net assets | 5.8M USD |  |
| e6fb382a:7 | Good Shepherd's total assets were $8,793,632. | $8,793,632 total assets | 8.8M USD |  |
| e6fb382a:8 | Good Shepherd's total liabilities were $2,985,976. | $2,985,976 total liabilities | 3M USD |  |
| e6fb382a:9 | Good Shepherd's non-operating revenue was $920,244. | $920,244 non-operating revenue | 920.2K USD |  |
| e6fb382a:10 | Good Shepherd's change in net assets was $573,981. | $573,981 change in net assets | 574K USD |  |
| e3665a33:0 | The YMCA of Greensboro served 38,421 members across 7 branches and 1 o | 38,421 total members served | 38.4K total members se |  |
| e3665a33:1 | The Annual Giving Campaign raised $657,713 for financial assistance, s | $657,713 annual giving campaign raised | 657.7K USD |  |
| e3665a33:2 | Financial assistance served 1,948 individuals and families in Guilford | 1,948 individuals/families assisted | 1.9K individuals/famil |  |
| e3665a33:3 | 5,791 children developed sportsmanship through youth sports. | 5,791 youth sports participants | 5.8K youth sports part |  |
| e3665a33:4 | 3,324 children learned lifesaving skills through swim lessons. | 3,324 swim lesson participants | 3.3K swim lesson parti |  |
| e3665a33:5 | 1,763 children developed a sense of belonging at Camp Weaver through o | 1,763 camp weaver children served | 1.8K camp weaver child |  |
| e3665a33:6 | 1,272 children learned their full potential in afterschool and summer  | 1,272 afterschool/summer camp children | 1.3K afterschool/summe |  |
| e3665a33:7 | Bright Beginnings provided 683 underserved children with school suppli | 683 bright beginnings children served | 683 bright beginnings  |  |
| e3665a33:8 | 941 children learned important skills in Safety Around Water lessons a | 941 safety around water participants | 941 safety around wate |  |
| e3665a33:9 | The association served more than 19,000 free summer meals to summer da | 19,000 free summer meals served | 19K free summer meals  |  |
| e3665a33:10 | Camp Weaver experienced record enrollment with 1,500 total camper sess | 1,500 camp weaver summer sessions | 1.5K camp weaver summe |  |
| 31855611:0 | Wesley Health Center provided services to 7,593 unduplicated patients  | 7,593 unduplicated patients served | 7.6K unduplicated pati |  |
| 31855611:1 | 92% of Wesley Health Center patients were uninsured. | 92% uninsured patient percentage | 92% |  |
| 31855611:2 | 76% of Wesley Health Center patients live at or below 100% of the Fede | 76% patients below poverty level | 76% |  |
| 31855611:3 | The food pantry distributed 1,022 emergency food bags, serving 3,884 i | 3,884 food pantry individuals served | 3.9K food pantry indiv |  |
| 31855611:5 | The After-School Program served a daily average of 30 youth. | 30 after-school program daily average | 30 after-school progra |  |
| 31855611:6 | Total volunteer hours contributed to Wesley Community Center were 7,24 | 7,245 total volunteer hours | 7.2K total volunteer h |  |
| 31855611:7 | The dollar value of volunteer hours was $157,869. | $157,869 volunteer hours dollar value | 157.9K USD |  |
| c94ad3f6:0 | DSVS direct services staff served 131 clients in 2021, providing shelt | 131 clients served | 131 clients served |  |
| c94ad3f6:1 | Volunteers answered 588 helpline calls in 2021. | 588 helpline calls answered | 588 helpline calls ans |  |
| c94ad3f6:2 | The Power Up, Speak Out! program is used in schools across Montana and | 8,000 students reached annually | 8K students reached an |  |
| c94ad3f6:3 | The Fun Run fundraiser raised more than $28,500 in 2021. | $28,500 fun run fundraising | 28.5K USD |  |
| c94ad3f6:4 | The end-of-year campaign raised almost $11,600 in additional support. | $11,600 end-of-year campaign donations | 11.6K USD |  |
| c94ad3f6:5 | The Home Again Thrift Store brought in $23,721 in 2021. | $23,721 thrift store revenue | 23.7K USD |  |
| c94ad3f6:6 | Volunteers contributed 5,555 helpline hours in 2021. | 5,555 volunteer helpline hours | 5.6K volunteer helplin |  |
| c94ad3f6:7 | At least eight perpetrators were charged with felony strangulation amo | 8 felony strangulation charges | 8 felony strangulation |  |
| c94ad3f6:8 | Seven families participated in the free parenting classes hosted with  | 7 families in parenting classes | 7 families in parentin |  |
| c94ad3f6:9 | 16 mentoring pairs met in Red Lodge middle and high schools, with ment | 16 mentoring pairs | 16 mentoring pairs |  |
| 9fcf82e5:0 | 286 clients were served over 3,796 hours of mental health services. | 286 clients served | 286 clients served |  |
| 9fcf82e5:1 | 54 clients were seen for free through the Victims of Crime Act. | 54 free clients served | 54 free clients served |  |
| 9fcf82e5:2 | 2 clinical interns were hired post-graduation. | 2 interns hired | 2 interns hired |  |
| 9fcf82e5:3 | 28 students were served by 11 volunteer mentors. | 28 students mentored | 28 students mentored |  |
| 9fcf82e5:4 | 22 families received long-term case navigation. | 22 families navigated | 22 families navigated |  |
| 9fcf82e5:5 | 131 referrals were made for food and basic needs help. | 131 basic needs referrals | 131 basic needs referr |  |
| 9fcf82e5:6 | 100% of callers reported a reduced sense of food insecurity. | 100% food insecurity reduction | 100% |  |
| 9fcf82e5:7 | 26 children were enrolled in free summer camp. | 26 summer camp children | 26 summer camp childre |  |
| 9fcf82e5:8 | 192 hours of summer enrichment activities were provided. | 192 enrichment hours | 192 enrichment hours |  |
| 9fcf82e5:9 | 81% of expenses went directly to programs. | 81% program expense ratio | 81% |  |
| e43d7482:0 | Summerhouse served 33 adults with intellectual and developmental disab | 33 members served | 33 members served |  |
| e43d7482:1 | 100% of members volunteer in the community or have paid employment. | 100% members engaged in community | 100% |  |
| e43d7482:2 | Members earned an average wage of $9.82 per hour through supported emp | $9.82 average hourly wage | 9.82 USD |  |
| e43d7482:3 | Summerhouse members worked at 18 different employers in the community. | 18 total employers | 18 total employers |  |
| e43d7482:4 | The organization added 14 new community-based volunteer sites in 2021. | 14 new volunteer sites | 14 new volunteer sites |  |
| e43d7482:5 | Summerhouse's shredding program recycled 60,000 pounds of paper and pr | 60,000 paper recycled (pounds) | 60K paper recycled (po |  |
| e43d7482:7 | 55% of members are on the Autism Spectrum. | 55% members on autism spectrum | 55% |  |
| e43d7482:8 | The average age of members is 26.1 years. | 26.1 average member age | 26.1 average member ag |  |
| ee33d0e3:0 | The Emergency Rental Assistance Program served 885 Marin households, p | 885 households served | 885 households served |  |
| ee33d0e3:1 | Over 85% of ERAP funds were distributed to those with the lowest incom | 85% funds to lowest income | 85% |  |
| ee33d0e3:2 | Female-headed households were 67% of those receiving ERAP assistance. | 67% female-headed households | 67% |  |
| ee33d0e3:3 | ERAP assistance went to households that identified as 43% Hispanic/Lat | 43% hispanic/latino households | 43% |  |
| ee33d0e3:4 | Between November 2022 and January 2024, $6,242,591 in ERAP funding was | $6,242,591 total funds disbursed | 6.2M USD |  |
| ee33d0e3:5 | The total 885 households receiving ERAP funds represented 2,149 indivi | 2,149 individuals served | 2.1K individuals serve |  |
| ee33d0e3:6 | 79% of ERAP assistance went toward arrears payments for rent and/or ut | 79% arrears payments | 79% |  |
| b670f181:0 | 479 pre-school aged children are better prepared to start kindergarten | 479 children prepared for kindergarten | 479 children prepared  |  |
| b670f181:1 | Over 5,328 individuals from 2,604 households were served by HRA progra | 5,328 individuals served | 5.3K individuals serve |  |
| b670f181:2 | Over 780 Energy Assistance Applications were completed, with over $3.0 | $780 energy assistance applications | 780 USD |  |
| b670f181:3 | Over 530 Federal and State Income Tax Returns were completed through t | 530 tax returns completed | 530 tax returns comple |  |
| b670f181:4 | Students showed an improvement of nearly 20% in all areas of developme | 20% developmental improvement percentage | 20% |  |
| 131309e3:0 | Community Support Services provided circles of support to 668 individu | 668 individuals served | 668 individuals served |  |
| 131309e3:1 | CSS delivered 447,572 total service hours across all programs. | 447,572 total service hours | 447.6K total service h |  |
| 131309e3:2 | CSS is one of the largest providers of Respite services in Illinois, p | 38,655 respite service hours | 38.7K respite service  |  |
| 131309e3:3 | CSS operates eight group homes providing 24-hour residential care, tot | 342,845 24-hour residential care hours | 342.8K 24-hour residen |  |
| 131309e3:4 | CSS employs 150 professionals, including Direct Support Professionals  | 150 total employees | 150 total employees |  |
| 131309e3:5 | Thirty Direct Support Professionals have been with CSS over ten years, | 30 dsps with 10+ years tenure | 30 dsps with 10+ years |  |
| 131309e3:6 | As many as 20,000 children and adults are on the Illinois PUNS list wa | 20,000 people on illinois PUNS waitlist | 20K people on illinois |  |
| 131309e3:7 | CSS staff schedule and transport participants to as many as 400 routin | 400 annual medical exams facilitated | 400 annual medical exa |  |
| b60a49eb:0 | Childhelp reached a milestone of more than 10 million children served  | 10,000,000 total children served | 10M total children ser |  |
| b60a49eb:1 | The Childhelp National Child Abuse Hotline handled 88,669 calls in FY2 | 88669% hotline calls handled | 88669% |  |
| b60a49eb:2 | For each dollar expended, over 92.5 cents is invested into serving chi | $92.5 cents per dollar to programs | 92.5 USD |  |
| b60a49eb:3 | The Childhelp Speak Up Be Safe for Athletes program reached 60,669 par | 60,669 athletes program participants | 60.7K athletes program |  |
| b60a49eb:4 | 37% of foster children exiting the Childhelp Children's Foster Family  | 37% foster child adoption rate | 37% |  |
| b60a49eb:5 | 80% of youth living at the Childhelp Merv Griffin Village showed signi | 80% clinical improvement rate | 80% |  |
| b60a49eb:6 | 96% of parents/caregivers who enrolled completed the Strengthening Fam | 96% parent program completion rate | 96% |  |
| b60a49eb:7 | 75% of students participating in the pilot program rated Childhelp Spe | 75% student program satisfaction | 75% |  |
| b60a49eb:8 | The Childhelp National Child Abuse Hotline turned 35 years old and now | 170 hotline languages supported | 170 hotline languages  |  |
| b60a49eb:9 | Representatives from 24 countries have visited Childhelp to study and  | 24 countries studying childhelp | 24 countries studying  |  |
| 0793289a:0 | 86% of residents maintained permanent and stable housing. | 86% housing stability rate | 86% |  |
| 0793289a:1 | QuestCDC was recognized as one of 7 top agencies for data quality amon | 7 data quality ranking | 7 data quality ranking |  |
| 0793289a:3 | There was a 50% increase in residents connected to essential resources | 50% resource connection increase | 50% |  |
| 0793289a:5 | There was a 20% increase in community partnerships and collaborations  | 20% partnership increase | 20% |  |
| 0793289a:6 | 78% of Quest Cares staff are now certified in Mental Health First Aid. | 78% staff mental health certification | 78% |  |
| 0793289a:7 | There was a 15% increase in clients receiving income support. | 15% income support increase | 15% |  |
| 0793289a:8 | Annual Block Party attendance increased by 30%. | 30% block party attendance increase | 30% |  |
| 0793289a:9 | 30% of clients completed early voting at the first Voter Registration  | 30% early voting participation | 30% |  |
| d8f908ed:0 | San Francisco's 2019 total homeless population is 9,784, the highest i | 9,784 total homeless population | 9.8K total homeless po |  |
| d8f908ed:1 | San Francisco reported 8,011 people that met the federal definition of | 17% homelessness increase percentage | 17% |  |
| d8f908ed:2 | Holy Family Day Home has trimmed more than 30% of excess administratio | 30% administration cost reduction | 30% |  |
| d8f908ed:3 | Food Runners is currently delivering over 17 tons of food a week that  | 17 weekly food rescued in tons | 17 weekly food rescued |  |
| 9dade083:0 | Jewish Family Service of the Lehigh Valley distributed 76,000 pounds o | 76,000 food distributed (lbs) | 76K food distributed ( |  |
| 9dade083:1 | The food pantry served 614 individuals, including 216 children, across | 614 individuals served by food pantry | 614 individuals served |  |
| 9dade083:2 | Older adult case management served 130 clients. | 130 case management clients | 130 case management cl |  |
| 9dade083:3 | Volunteers contributed 2,171 hours of service. | 2,171 volunteer hours | 2.2K volunteer hours |  |
| 9dade083:4 | The organization provided 164 ShareCare rides for older adults. | 164 sharecare rides | 164 sharecare rides |  |
| 9dade083:5 | Friendly visits to older adults totaled 64. | 64 friendly visits | 64 friendly visits |  |
| 9dade083:6 | Outreach calls to older adults numbered 349. | 349 outreach calls | 349 outreach calls |  |
| 9dade083:7 | Jewish Holiday Outreach programs reached 207 participants. | 207 holiday outreach participants | 207 holiday outreach p |  |
| 68e0e142:0 | Over 500 young men have participated in the eight-month, tuition-free  | 500 total students served | 500 total students ser |  |
| 68e0e142:1 | After Narrow Gate, 95% of graduates are more confident in their identi | 95% identity confidence rate | 95% |  |
| 68e0e142:2 | After Narrow Gate, 89% of graduates are able to hear from God quite a  | 89% ability to hear from god | 89% |  |
| 68e0e142:3 | After Narrow Gate, 89% of graduates make decisions based on Biblical p | 89% biblical decision-making rate | 89% |  |
| 68e0e142:4 | After Narrow Gate, 85% of graduates are more confident in overcoming c | 85% confidence in overcoming challenges | 85% |  |
| 68e0e142:5 | After Narrow Gate, 92% of graduates have grown in perseverance. | 92% perseverance growth rate | 92% |  |
| 68e0e142:6 | After Narrow Gate, over 90% of graduates are part of a vulnerable and  | 90% community involvement rate | 90% |  |
| 68e0e142:7 | After Narrow Gate, more than 90% of graduates understand the Bible qui | 90% bible understanding rate | 90% |  |
| 68e0e142:8 | Narrow Gate has sold and distributed over 10,000 Bibles since 2013. | 10,000 bibles distributed | 10K bibles distributed |  |
| 68e0e142:9 | TN Box Beams has built over 10,000 beams and ships across the country, | 10,000 box beams built | 10K box beams built |  |
| 68e0e142:10 | The Narrow Gate property spans 122 acres in Middle Tennessee. | 122 property acreage | 122 property acreage |  |
| 040633b7:0 | 14,102 individuals were impacted by YMCA programs and membership. | 14,102 individuals impacted | 14.1K individuals impa |  |
| 040633b7:1 | The Y provided $749,607 in financial assistance and subsidies to ensur | $749,607 financial assistance provided | 749.6K USD |  |
| 040633b7:2 | People in 15 Montana counties benefited from Y programs. | 15 counties served | 15 counties served |  |
| 040633b7:5 | 2,380 young athletes participated in youth basketball and soccer progr | 2,380 youth athletes served | 2.4K youth athletes se |  |
| 040633b7:6 | 1,367 life-saving swim lessons were taught to Missoulians of all ages. | 1,367 swim lessons taught | 1.4K swim lessons taug |  |
| 040633b7:7 | 3,511 group fitness classes were offered to help members build healthy | 3,511 group fitness classes offered | 3.5K group fitness cla |  |
| 040633b7:8 | 459 3rd graders learned water and boat safety skills through the SPLAS | 459 SPLASH! participants | 459 SPLASH! participan |  |
| 040633b7:9 | The Here for Good Capital Campaign raised $13.8 million from 680 donor | $13,800,000 capital campaign funds raised | 13.8M USD |  |
| 040633b7:10 | 60 campaigners raised $348,328 during the 2022 Annual Support Campaign | $348,328 annual campaign funds raised | 348.3K USD |  |
| da2ba72a:0 | Abbott House served 74 girls in its residential program in FY2017. | 74 residential program girls served | 74 residential program |  |
| da2ba72a:1 | Abbott House served 35 girls in its aftercare program in FY2017. | 35 aftercare program girls served | 35 aftercare program g |  |
| da2ba72a:2 | Abbott House served 10 girls in its independent living program in FY20 | 10 independent living girls served | 10 independent living  |  |
| da2ba72a:3 | The residential program had a 67% occupancy rate in FY2017. | 67% residential occupancy rate | 67% |  |
| da2ba72a:4 | 86% of Abbott House's funding came from fees in FY2017. | 86% funding from fees | 86% |  |
| da2ba72a:5 | 58% of Abbott House's operating expenses went to the residential progr | 58% expenses for residential program | 58% |  |
| 015893d4:0 | MFAN's MilCents financial literacy program had more than 9,000 users s | 9,000 milcents users | 9K milcents users |  |
| 015893d4:1 | MFAN's total expenses in 2016 were $589,934.45. | $589934 total expenses | 589.9K USD |  |
| 015893d4:4 | 100 percent of MFAN's donations went directly to supporting military f | 100 donations to programs | 100 donations to progr |  |
| e6696bd9:0 | CRT served more than 86,000 individuals and over 35,000 families in 20 | 86,000 individuals served | 86K individuals served |  |
| e6696bd9:1 | CRT helped 680 individuals secure permanent housing in the past year. | 680 individuals in permanent housing | 680 individuals in per |  |
| e6696bd9:2 | CRT's Energy Assistance Program serves more than 23,000 families each  | 23000% families receiving energy assistance | 23000% |  |
| e6696bd9:3 | CRT weatherized a total of 176 homes in Connecticut at a cost of $1,59 | $176 homes weatherized | 176 USD |  |
| e6696bd9:4 | CRT's Medication Assisted Treatment (MAT) program has 74 patients; abs | 74% MAT program patients | 74% |  |
| e6696bd9:5 | Volunteers contributed over 125,000 hours of their time, valued at mor | $125,000 volunteer hours contributed | 125K USD |  |
| e6696bd9:6 | CRT's core service area covers 40 cities and towns, connecting to peop | 40 core service area towns | 40 core service area t |  |
| e6696bd9:8 | CRT helped 298 individuals avoid eviction and aided 667 individuals in | 298 individuals avoiding eviction | 298 individuals avoidi |  |
| e6696bd9:9 | Of those served, 59% were female, and 32% of the families served were  | 59% female clients percentage | 59% |  |
| 56688514:0 | Council on Aging helped 19,831 people remain independent in their home | 19,831 people served at home | 19.8K people served at |  |
| 56688514:1 | Council on Aging was Ohio's top performer in reducing unnecessary nurs | 22% nursing home placement rate | 22% |  |
| 56688514:2 | Council on Aging's Care Transitions program achieved a 12 percent hosp | 12% hospital readmission rate | 12% |  |
| 56688514:3 | Council on Aging assessed 290 nursing home residents for Community Tra | 190 nursing home residents transitioned home | 190 nursing home resid |  |
| 56688514:4 | Council on Aging's Aging and Disabilities Resource Network responded t | 35,203 information and referral requests | 35.2K information and  |  |
| 56688514:5 | Council on Aging contracted with and monitored nearly 200 provider org | 1,970,000 home-delivered meals | 2M home-delivered meal |  |
| 56688514:6 | Council on Aging served 198,939 congregate meals and provided 299,347  | 299,347 transportation trips | 299.3K transportation  |  |
| 56688514:8 | In the first nine months of 2013, 1,669 patients completed the 30-day  | 1,669 care transitions completions | 1.7K care transitions  |  |
| 56688514:9 | Total support and revenue for Council on Aging in 2013 was $101,077,33 | $101,077,339 total support and revenue | 101.1M USD |  |
| 2995644a:0 | Kendal~Crosslands Communities raised over $542,500 in charitable gifts | $542,500 total charitable gifts | 542.5K USD |  |
| 2995644a:1 | The organization's philanthropic funds total more than $23 million. | $23,000,000 total philanthropic funds | 23M USD |  |
| 2995644a:2 | The $56 million Health Center and Garden Apartments project at Kendal  | $56,000,000 capital project investment | 56M USD |  |
| 2995644a:3 | The Crosslands Reserve Fund provided $577,000 in financial assistance  | $577,000 resident financial assistance | 577K USD |  |
| 2995644a:4 | The Kendal at Longwood Reserve Fund provided $220,000 in financial ass | $220,000 resident financial assistance | 220K USD |  |
| 2995644a:5 | The Shed at Crosslands raised over $20,000 for the American Friends Se | $20,000 funds raised for AFSC | 20K USD |  |
| 2995644a:6 | Kendal at Longwood installed 114 solar panels on the Health Center, wi | 114 solar panels installed | 114 solar panels insta |  |
| 2995644a:7 | The Helping Hands program served approximately 60 lower-income senior  | 60 households served by helping hands | 60 households served b |  |
| 2995644a:8 | The Louise P. Mullestein Scholarship Fund helped 11 staff members with | $11 staff assisted with child care | 11 USD |  |
| 2995644a:9 | The A. & E. du Pont Kendal Staff Support Fund disbursed $14,593 for 13 | $13 staff emergency assistance requests | 13 USD |  |
| 93774f84:0 | FACT Oregon served families across 34 of Oregon's 36 counties. | 34 counties served | 34 counties served |  |
| 93774f84:1 | FACT Oregon served families in 129 school districts. | 129 school districts served | 129 school districts s |  |
| 93774f84:2 | 52% of families served called out behavior as an area of needed suppor | 52% families needing behavior support | 52% |  |
| 93774f84:3 | 48% of families served navigate disability alongside other marginalize | 48% families with intersecting identities | 48% |  |
| d5cb6d0c:0 | Elevate Youth Services provided 468 youth with support and guidance la | 468 youth served | 468 youth served |  |
| d5cb6d0c:1 | The organization provided 5,128 nights of housing to youth. | 5,128 nights of housing | 5.1K nights of housing |  |
| d5cb6d0c:2 | Elevate Youth Services delivered 4,435 hours of direct services. | 4,435 hours of direct services | 4.4K hours of direct s |  |
| d5cb6d0c:3 | 97% of youth exiting intensive programs have a supportive and stable c | 97% youth with stable adult connection | 97% |  |
| d5cb6d0c:4 | 90% of youth exiting intensive programs learned new methods of problem | 90% youth learned problem-solving skills | 90% |  |
| d5cb6d0c:5 | 89% of youth exiting intensive programs can identify their own values  | 89% youth with self-awareness | 89% |  |
| d5cb6d0c:6 | 88% of youth exiting intensive programs feel they have control over th | 88% youth with optimism and control | 88% |  |
| d5cb6d0c:7 | 88% of youth exiting intensive programs have connections to activities | 88% youth with community connections | 88% |  |
| d5cb6d0c:8 | 85% of youth exiting intensive programs demonstrate skills to interact | 85% youth with interpersonal skills | 85% |  |
| d5cb6d0c:10 | 79% of youth receiving intensive services were low income. | 79% low-income youth served | 79% |  |
| d5cb6d0c:11 | 58% of youth receiving intensive services were involved with foster ca | 58% youth in foster care system | 58% |  |
| d5cb6d0c:12 | 37% of youth receiving intensive services were homeless or at risk of  | 37% youth experiencing homelessness | 37% |  |
| db75f054:0 | The average hourly wage in Dubuque County increased to $18.86, surpass | $18.86 average hourly wage | 18.86 USD |  |
| db75f054:2 | In the first year of the five-year campaign, $232,095,950 in new const | $232,095,950 new construction value | 232.1M USD |  |
| db75f054:3 | The workforce grew to 59,300 in the first year of the campaign, toward | 59,300 workforce size | 59.3K workforce size |  |
| db75f054:5 | Over 100 internship positions were posted on AccessDubuqueJobs.com sin | 100 internships posted | 100 internships posted |  |
| db75f054:6 | Greater Dubuque Development secured three new companies through its Na | 3 new companies attracted | 3 new companies attrac |  |
| db75f054:7 | A $2.3-million fiber enhancement project by CenturyLink was completed, | $2,300,000 telecom infrastructure investment | 2.3M USD |  |
| 2cf7c1e8:0 | Forward Stride served 506 clients in total. | 506 total clients served | 506 total clients serv |  |
| 2cf7c1e8:1 | Clients served increased by 10% over the previous year. | 10% year-over-year client increase | 10% |  |
| 2cf7c1e8:2 | Volunteers contributed 10,942 program hours. | 10,942 total program hours | 10.9K total program ho |  |
| 2cf7c1e8:3 | Forward Stride served 351 clients through its own programs. | 351 forward stride clients | 351 forward stride cli |  |
| 2cf7c1e8:5 | The Nate Asby Scholarship Fund supported 29 clients in 2025. | 29 scholarship clients supported | 29 scholarship clients |  |
| 2cf7c1e8:6 | 52 volunteers have been retained for 5 or more years. | 52 volunteers retained 5+ years | 52 volunteers retained |  |
| 2cf7c1e8:7 | 29 volunteers have been retained for 10 or more years. | 29 volunteers retained 10+ years | 29 volunteers retained |  |
| c19607d8:0 | Fair Chance served 31 organizations, reaching nearly 14,000 children,  | 14,000 children, youth, and families reached | 14K children, youth, a |  |
| c19607d8:1 | Nonprofits completing the Pathways Partnership reported an increase of | 25% increase in best practices (pathways) | 25% |  |
| c19607d8:3 | Nonprofits completing the Praxis Partnership reported an increase of 2 | 25% increase in best practices (praxis) | 25% |  |
| ec5c8930:0 | Guadalupe Centers celebrated a 92.5% graduation rate at its charter hi | 92.5% high school graduation rate | 92.5% |  |
| ec5c8930:1 | The Family Support Center served 515 households, with 94% receiving fo | 515% households served | 515% |  |
| ec5c8930:2 | The Workforce Department provided employment and financial counseling  | 180 individuals counseled | 180 individuals counse |  |
| ec5c8930:3 | The mental health program provided 841 counseling sessions to youth an | 841 counseling sessions | 841 counseling session |  |
| ec5c8930:4 | The Older Adults Program provided 21,295 meals to participants. | 21,295 meals provided | 21.3K meals provided |  |
| ec5c8930:5 | The Youth Development Program served 1,293 youth in 2024. | 1,293 youth served | 1.3K youth served |  |
| ec5c8930:6 | The Early Childhood Center served 153 children, all qualifying for Hea | 153 children served | 153 children served |  |
| ec5c8930:7 | Guadalupe Centers Elementary School served 709 students, with 80% Engl | 709% elementary students | 709% |  |
| ec5c8930:8 | Guadalupe Centers High School served 445 students and achieved a 92% g | 445% high school students | 445% |  |
| ec5c8930:9 | The Outpatient Treatment program served 108 individuals, 97% Spanish-s | 108% treatment clients | 108% |  |
| ec5c8930:10 | Villa View Apartments, a 50-unit mixed-income development, broke groun | 50 new housing units | 50 new housing units |  |
| ec5c8930:11 | The 50/50 Dual Language Model was implemented at the Early Childhood C | 50 dual language model | 50 dual language model |  |
| d58e3d5b:0 | Hearts With A Mission served 79 individuals through Safe Families for  | 79 SFFC individuals served | 79 SFFC individuals se |  |
| d58e3d5b:1 | Safe Families for Children provided 5,010 night hostings in one county | 5,010 SFFC night hostings | 5K SFFC night hostings |  |
| d58e3d5b:2 | Safe Families for Children served 135 individuals in another county, w | 135 SFFC individuals served (second county) | 135 SFFC individuals s |  |
| d58e3d5b:3 | Safe Families for Children provided 8,412 night hostings in a second c | 8,412 SFFC night hostings (second county) | 8.4K SFFC night hostin |  |
| d58e3d5b:4 | Safe Families for Children served 113 individuals in a third county, w | 113 SFFC individuals served (third county) | 113 SFFC individuals s |  |
| d58e3d5b:5 | Safe Families for Children provided 2,706 night hostings in a third co | 2,706 SFFC night hostings (third county) | 2.7K SFFC night hostin |  |
| d58e3d5b:6 | 95% of children hosted through Safe Families for Children return to th | 95% SFFC return rate | 95% |  |
| d58e3d5b:7 | The average stay for a child in a Safe Families host home is slightly  | 45 average SFFC stay (days) | 45 average SFFC stay ( |  |
| d58e3d5b:9 | Hearts With A Mission operates a 15-bed emergency shelter for youth ag | 15 emergency shelter beds | 15 emergency shelter b |  |
| d58e3d5b:10 | The Transitional Living Program serves youth ages 18-22 with 12-bed ho | 12 TLP beds per home | 12 TLP beds per home |  |
| 8e1cf08b:0 | Luther Manor completed a $9 million campus-wide renovation, Project Re | $9,000,000 campus renovation investment | 9M USD |  |
| 8e1cf08b:2 | 82 volunteers served over 9,000 hours at Luther Manor in 2024, with a  | $82 active volunteers | 82 USD |  |
| 8e1cf08b:3 | The first annual dinner auction raised $27,315 in net proceeds for the | $27,315 dinner auction proceeds | 27.3K USD |  |
| 8e1cf08b:5 | The Luther Manor Foundation held total net assets of $8,971,558 as of  | $8,971,558 foundation net assets | 9M USD |  |
| 8e1cf08b:6 | Luther Manor launched the Catalyst leadership development program, wit | 3 catalyst program cohorts | 3 catalyst program coh |  |
| e27aec13:0 | WAGES direct service grantees served 948 women since the beginning of  | 948% women directly served | 948% |  |
| e27aec13:1 | An estimated 3,143 individuals benefitted from WAGES direct service gr | 3,143 total individuals benefitted | 3.1K total individuals |  |
| e27aec13:2 | Most WAGES participants are women of color, with 49.5% identifying as  | 49.5% latinx/hispanic participants | 49.5% |  |
| e27aec13:3 | At program entry, 61% of participants had a high school diploma/GED or | 61 participants with HS or less | 61 participants with H |  |
| e27aec13:4 | The top four barriers to economic security at program entry were acces | 57% housing barrier rate | 57% |  |
| e27aec13:6 | 354 WAGES participants (48%) reached their educational goals cumulativ | 354% participants with educational gains | 354% |  |
| e27aec13:7 | 36% of WAGES participants (93 women) earned at least $15.01 per hour a | 36 participants earning $15+/hour | 36 participants earnin |  |
| e27aec13:8 | 94 WAGES participants reduced public supports, including two women who | 94 participants reducing public supports | 94 participants reduci |  |
| e27aec13:9 | At program exit, the share of participants earning $1,000 or less per  | 37 participants earning under $1k at intake | 37 participants earnin |  |
| 93e81f02:0 | Women's Support Services worked with 527 clients, including 41 childre | 527 clients served | 527 clients served |  |
| 93e81f02:1 | The organization delivered 794 individual counseling sessions. | 794 counseling sessions | 794 counseling session |  |
| 93e81f02:2 | WSS provided court-based advocacy and support to 250 victims of family | 250 court advocacy clients | 250 court advocacy cli |  |
| 93e81f02:4 | WSS responded to approximately 420 hotline calls last year. | 420 hotline calls | 420 hotline calls |  |
| 93e81f02:5 | The Trade Secrets event raised more than $275,000, well over one-third | $275,000 event fundraising | 275K USD |  |
| 93e81f02:6 | Total annual revenue was $614,807. | $614,807 total revenue | 614.8K USD |  |
| 93e81f02:7 | Total annual expenses were $604,954. | $604,954 total expenses | 605K USD |  |
| 4b310b24:1 | NHTDWG had 61 general members from across the U.S. in 2022, including  | 61 total members | 61 total members |  |
| 4b310b24:2 | 219 participants attended NHTDWG's 3-part Spring Webinar Series. | 219 webinar participants | 219 webinar participan |  |
| 4b310b24:5 | NHTDWG provided accessibility training to 10 individuals on making eve | 10 accessibility training attendees | 10 accessibility train |  |
| 590e8b9e:0 | The Association assisted 3,871 clients aged 60-103 in Region IV. | 3,871 clients served | 3.9K clients served |  |
| 590e8b9e:1 | Membership grew 16% over 2011, reaching 904 members. | 904% total members | 904% |  |
| 590e8b9e:2 | Volunteers donated 10,023 hours of service. | 10,023 volunteer hours | 10K volunteer hours |  |
| 590e8b9e:3 | Senior dining meals served totaled 89,151 to 2,622 clients at 23 sites | 89,151 senior dining meals | 89.2K senior dining me |  |
| 590e8b9e:4 | Meals on Wheels provided 53,591 meals to 526 homebound seniors. | 53,591 meals on wheels delivered | 53.6K meals on wheels  |  |
| 590e8b9e:5 | The Resources department served 1,227 clients with 14,597 units of ser | 1,227 resources clients served | 1.2K resources clients |  |
| 590e8b9e:6 | Health services reached 948 people, primarily through footcare. | 948 health clients served | 948 health clients ser |  |
| 590e8b9e:7 | Activity/Life Enrichment programs drew 29,743 senior participants. | 29,743 activity program participants | 29.7K activity program |  |
| 590e8b9e:8 | Transportation Fare Assistance provided 2,860 rides for 91 people. | 2,860 subsidized rides | 2.9K subsidized rides |  |
| 590e8b9e:9 | The Silver Campaign raised $58,507, surpassing its goal with a $25,000 | $58,507 silver campaign funds raised | 58.5K USD |  |
| 590e8b9e:10 | A $99,000 CDBG grant funded window replacement and parking/sidewalk re | $99,000 CDBG grant for building repairs | 99K USD |  |
| efd61fea:5 | Greater Dubuque Development conducted 256 Info Action visits with busi | 256 info action visits | 256 info action visits |  |
| efd61fea:6 | Greater Dubuque Development provided assistance on 474 occasions to 21 | 474 assistance occasions | 474 assistance occasio |  |
| efd61fea:8 | Company expansions generated $27,750,000 in capital investment. | $27,750,000 capital investment from expansions | 27.8M USD |  |
| efd61fea:9 | Over 75 area businesses participated in Dubuque Innovation Consortium  | 75 consortium business participants | 75 consortium business |  |
| efd61fea:12 | 86 participants completed the Distinctively Dubuque program. | 86 distinctively dubuque graduates | 86 distinctively dubuq |  |
| efd61fea:13 | 1,800 net new jobs were created in 2010. | 1,800 net new jobs created | 1.8K net new jobs crea |  |
| efd61fea:14 | The average wage reached $17.57 per hour, exceeding the $16.00 goal. | $17.57 average hourly wage | 17.57 USD |  |
| efd61fea:15 | Capital investment reached $254,102,007, ahead of the five-year $300 m | $254,102,007 capital investment total | 254.1M USD |  |
| 715b2531:0 | On average 17 women were on the waitlist for YWCA Helena services. | 17 average waitlist size | 17 average waitlist si |  |
| 715b2531:1 | 65 parents completed our parenting classes. | 65 parents completing classes | 65 parents completing  |  |
| 715b2531:2 | 23 children reunified with their parents. | 23 children reunified with parents | 23 children reunified  |  |
| 715b2531:3 | 100% of children served at the Clubhouse were 150% below the federal p | 100% children below poverty line | 100% |  |
| 715b2531:4 | 22 children were housed at YWCA Helena's shelter. | 22 children in shelter | 22 children in shelter |  |
| 715b2531:5 | 41 children were served by Caterpillars Child Care. | 41 children in child care | 41 children in child c |  |
| 715b2531:6 | 16 children were served by Caterpillars Counseling. | 16 children in counseling | 16 children in counsel |  |
| 715b2531:7 | The cost to raise $1 is $0.09. | $0.09 cost per dollar raised | 0.09 USD |  |
| 715b2531:8 | In 2022, 143 housing-insecure individuals were counted in the Point in | 143 housing-insecure individuals | 143 housing-insecure i |  |
| 715b2531:9 | The Helena School District reported 283 children as insecurely housed. | 283 insecurely housed children | 283 insecurely housed  |  |
| ebb0bcfe:0 | A Precious Child served 39,097 children in 2016, a 16% increase from t | 39097% children served | 39097% |  |
| ebb0bcfe:1 | 180,000 Colorado children live in poverty. | 180,000 colorado children in poverty | 180K colorado children |  |
| ebb0bcfe:3 | A Precious Child operates 33 boutiques serving 8 Denver metro area cou | 33 boutiques | 33 boutiques |  |
| ebb0bcfe:4 | The organization has 261 agency partners. | 261 agency partners | 261 agency partners |  |
| ebb0bcfe:9 | Total revenue for 2016 was $8,584,481. | $8,584,481 total revenue | 8.6M USD |  |
| ebb0bcfe:10 | Total expenses for 2016 were $8,432,726. | $8,432,726 total expenses | 8.4M USD |  |
| ebb0bcfe:11 | The value of donated essentials was $6,778,053. | $6,778,053 value of donated essentials | 6.8M USD |  |
| 28bb72ee:0 | The total state investment in the EITC program yields a return of $45  | $45 return on investment | 45 USD |  |
| 28bb72ee:1 | 3,700 taxpayers claimed the Earned Income Tax Credit through the Virgi | 3,700 taxpayers claiming EITC | 3.7K taxpayers claimin |  |
| 28bb72ee:2 | The program generated $6.5 million in EITC refunds for taxpayers. | $6,500,000 EITC refunds | 6.5M USD |  |
| 28bb72ee:3 | Taxpayers saved $4.9 million in tax preparation fees. | $4,900,000 tax prep fees saved | 4.9M USD |  |
| 28bb72ee:4 | The program secured $4.7 million in Child Tax Credit and Additional Ch | $4,700,000 child tax credit refunds | 4.7M USD |  |
| e4c31d31:2 | Community Options manages more than 650 homes for 1,800 people across  | 650 homes managed | 650 homes managed |  |
| e4c31d31:3 | Community Options guides more than 1,800 individuals into competitive  | 1,800 individuals employed | 1.8K individuals emplo |  |
| e4c31d31:4 | Community Options has 5,500 employees, donors, volunteers, and Board m | 5,500 staff and supporters | 5.5K staff and support |  |
| e4c31d31:5 | More than 90% of all funds are allocated to direct support staff and p | 90% funds to direct support | 90% |  |
| e4c31d31:6 | Total revenue and support for 2023 was $353,365,849. | $353,365,849 total revenue | 353.4M USD |  |
| e4c31d31:7 | Total expenses for 2023 were $336,113,947. | $336,113,947 total expenses | 336.1M USD |  |
| e4c31d31:8 | Net assets at the end of 2023 were $83,013,092. | $83,013,092 net assets | 83M USD |  |
| e5ec2221:0 | LIFE CIL impacted 3,803 lives from July 1, 2021 to June 30, 2022. | 3,803 total lives impacted | 3.8K total lives impac |  |
| e5ec2221:1 | LIFE CIL provided information and referral services over 2,880 times. | 2,880 information and referral services | 2.9K information and r |  |
| e5ec2221:2 | LIFE CIL provided durable medical equipment through the equipment loan | 1,400 durable medical equipment recipients | 1.4K durable medical e |  |
| e5ec2221:3 | LIFE CIL supported 241 seniors with vision loss in getting assistive t | 241 seniors with vision loss served | 241 seniors with visio |  |
| e5ec2221:4 | LIFE CIL provided individual advocacy services to 27 people. | 27 individual advocacy recipients | 27 individual advocacy |  |
| e5ec2221:5 | LIFE CIL taught 34 school-age youth about transitioning to adult lives | 34 youth transition program participants | 34 youth transition pr |  |
| e5ec2221:6 | LIFE CIL helped four people transition out of nursing homes into their | 4 nursing home transitions | 4 nursing home transit |  |
| e5ec2221:7 | 213 individuals received extended individual services this year. | 213 extended individual services recipients | 213 extended individua |  |
| 47fd5ae6:0 | Pregnancy Center Plus provided 15,982 total services to clients in the | 15,982 total services provided | 16K total services pro |  |
| 47fd5ae6:1 | Volunteers contributed 38,746 hours, valued at $774,920. | $38,746 volunteer hours | 38.7K USD |  |
| 865fb62f:0 | The Wellspring served 2,853 total clients in 2021. | 2,853 total clients served | 2.9K total clients ser |  |
| 865fb62f:1 | The Wellspring provided $1,043,459.13 in financial assistance to clien | $1.04346e+06 financial assistance provided | 1M USD |  |
| 865fb62f:2 | The Wellspring served 1,583 victims of crime in 2021. | 1,583 victims of crime served | 1.6K victims of crime  |  |
| 865fb62f:3 | The Wellspring's Homeless Services Program served 939 clients, includi | 939 homeless services clients | 939 homeless services  |  |
| 865fb62f:4 | The Wellspring's Domestic Violence Program sheltered 107 women, 2 men, | 2,157 domestic violence shelter nights | 2.2K domestic violence |  |
| 865fb62f:5 | The Wellspring's Rural Victim Services Program served 300 clients and  | 21,140 rural victim bed nights | 21.1K rural victim bed |  |
| 865fb62f:6 | The Wellspring's Counseling & Family Development Center provided 2,648 | 2,648 counseling sessions | 2.6K counseling sessio |  |
| 865fb62f:7 | The Wellspring's Youth Empowerment Program served 279 youth in positiv | 279 youth in development programs | 279 youth in developme |  |
| 865fb62f:8 | The Wellspring received 2,972 unduplicated crisis calls, including 1,2 | 2,972 crisis calls received | 3K crisis calls receiv |  |
| 865fb62f:9 | The Wellspring's total revenue for 2021 was $6,287,124. | $6,287,124 total revenue | 6.3M USD |  |
| 865fb62f:10 | The Wellspring's total expenses for 2021 were $6,082,612. | $6,082,612 total expenses | 6.1M USD |  |
| 865fb62f:11 | The Wellspring's net assets at the end of 2021 were $3,338,524. | $3,338,524 net assets | 3.3M USD |  |
| 865fb62f:12 | The Wellspring served clients across 12 parishes in Northeast Louisian | 12 parishes served | 12 parishes served |  |
| 865fb62f:13 | The Wellspring provided 7,057 volunteer hours in 2021. | 7,057 volunteer hours | 7.1K volunteer hours |  |
| 6b3b6732:0 | Coastal Georgia Area Community Action Authority served 714 Head Start  | 714 head start students served | 714 head start student |  |
| 6b3b6732:1 | The organization served 94 Early Head Start students in 2022. | 94 early head start students served | 94 early head start st |  |
| 6b3b6732:2 | 327 individuals received income management assistance through the VITA | 327 VITA program participants | 327 VITA program parti |  |
| 6b3b6732:3 | Total public and private support for the year ended June 30, 2022 was  | $26,190,556 total public and private support | 26.2M USD |  |
| 6b3b6732:4 | Total program expenses for the year ended June 30, 2022 were $17,756,1 | $17,756,179 total program expenses | 17.8M USD |  |
| 6b3b6732:5 | The Head Start program had expenses of $10,474,193 in 2022. | $10,474,193 head start program expenses | 10.5M USD |  |
| 6b3b6732:6 | The Low Income Home Energy Assistance Program (LIHEAP) had expenses of | $3,435,457 LIHEAP program expenses | 3.4M USD |  |
| 6b3b6732:8 | The organization operates three full-service kitchens for its meal pro | 3 full-service kitchens | 3 full-service kitchen |  |
| 6b3b6732:9 | The Georgia Pre-K program served children in seven classrooms across s | 7 georgia pre-k classrooms | 7 georgia pre-k classr |  |
| b42e6f95:0 | Calm Waters impacted 7,806 people in fiscal year 2025. | 7,806 total people impacted | 7.8K total people impa |  |
| b42e6f95:1 | Since 1992, Calm Waters has impacted 84,043 children and families. | 84,043 lifetime children & families impacted | 84K lifetime children  |  |
| b42e6f95:2 | 2,787 students and families benefitted from School Support Groups. | 2,787 school support group beneficiaries | 2.8K school support gr |  |
| b42e6f95:7 | 458 people were served through Community Partnership Programming. | 458 community partnership programming served | 458 community partners |  |
| b42e6f95:8 | 258 detained individuals received Incarcerated Grief Support. | 258 incarcerated grief support recipients | 258 incarcerated grief |  |
| b42e6f95:10 | 75 medical residents and students received Grief Support & Education. | 75 medical trainees served | 75 medical trainees se |  |
| b42e6f95:11 | 15 organizations were served through Community Partnerships. | 15 community partner organizations | 15 community partner o |  |
| 2ea6005f:0 | About 320 Tennesseans were employed through the program in fiscal year | 320 total employees | 320 total employees |  |
| 2ea6005f:1 | 65% of hours worked were by persons with disabilities. | 65% hours by people with disabilities | 65% |  |
| 2ea6005f:2 | Over $12,000,000 in gross revenues were generated by the program in FY | $12,000,000 gross revenues | 12M USD |  |
| 2ea6005f:3 | Gross revenues increased by nearly $300,000 over FY 2022. | $300,000 revenue increase from FY 2022 | 300K USD |  |
| 2ea6005f:4 | Nearly $3,000,000 in products were produced and over $9,000,000 in ser | $3,000,000 products produced | 3M USD |  |
| 2ea6005f:5 | Over $9,000,000 in services were provided statewide. | $9,000,000 services provided | 9M USD |  |
| 2ea6005f:6 | Total FY 2023 sales were $12.18 million. | $12,180,000 total sales | 12.2M USD |  |
| 2ea6005f:7 | The program serves 19 rest areas across the state of Tennessee. | 19 rest areas served | 19 rest areas served |  |
| 5d03563c:0 | The Family Resource Center averaged 53 visits per day by September 202 | 53 daily visits to family resource center | 53 daily visits to fam |  |
| 5d03563c:1 | MyChart Bedside activation rates in inpatient units increased from 31  | 40% mychart bedside activation rate | 40% |  |
| 5d03563c:3 | A total of 20 new members joined the Patient and Family Experience Cou | 20 new council members | 20 new council members |  |
| 8c29956a:1 | In 2019, AFEDJ supported 17 humanitarian institutions across the West  | 17 institutions supported | 17 institutions suppor |  |
| 8c29956a:2 | The Mother Empowerment Program served 417 families in 2019, providing  | 417 families served in mother empowerment program | 417 families served in |  |
| 8c29956a:3 | A $468,000 grant from the Harold C. Smith Foundation enabled Ahli Arab | $468,000 grant for ahli arab hospital services | 468K USD |  |
| 8c29956a:5 | The Jerusalem Princess Basma Centre's inclusive school serves over 450 | 450% students at princess basma centre | 450% |  |
| 8c29956a:6 | A $75,000 grant from the Louise H. and David S. Ingalls Foundation exp | $75,000 grant for christ school technology | 75K USD |  |
| 8c29956a:8 | St. Luke's Hospital in Nablus received a new ambulance in 2019, funded | $85,000 funds raised for st. luke's ambulance | 85K USD |  |
| fd032b14:0 | Clover served more than 8,000 individuals and families across its prog | 8,000 individuals and families served | 8K individuals and fam |  |
| fd032b14:2 | 108 children successfully transitioned from Clover Academy to kinderga | 90% kindergarten readiness rate | 90% |  |
| fd032b14:4 | Clover secured a nearly $500,000 investment from the W.K. Kellogg Foun | $500,000 kellogg foundation investment | 500K USD |  |
| fd032b14:5 | Clover's 125th Anniversary Campaign raised more than $2 million. | $2,000,000 anniversary campaign funds raised | 2M USD |  |
| fd032b14:6 | 373 families and seniors received VITA tax assistance, totaling $521,4 | $521,496 VITA tax refunds secured | 521.5K USD |  |
| fd032b14:7 | Clover's total income for FY 2023-2024 was $16,246,481. | $16,246,481 total annual income | 16.2M USD |  |
| 64670634:1 | Of the 64 youth served by the Family CARES Center in FY22, 83% success | 83% youth symptom reduction rate | 83% |  |
| 64670634:2 | Of the 64 youth served by the Family CARES Center in FY22, 85% came in | 85% youth with clinical concern | 85% |  |
| 64670634:3 | Of the 64 youth served by the Family CARES Center in FY22, 79% had exp | 79% youth with trauma exposure | 79% |  |
| 64670634:4 | The Coleman Foundation awarded Northwestern Settlement a $63,000 Proje | $63,000 coleman foundation grant | 63K USD |  |
| 64670634:5 | This year, 79 young children are enrolled in the Settlement's early ch | 79 early childhood enrollment | 79 early childhood enr |  |
| 64670634:6 | Total revenue for the year was $20,888,000. | $20,888,000 total revenue | 20.9M USD |  |
| 64670634:7 | Total expenses for the year were $22,049,000. | $22,049,000 total expenses | 22M USD |  |
| 64670634:8 | Program services expenses were $20,126,000. | $20,126,000 program services expenses | 20.1M USD |  |
| 64670634:9 | Donations and private sources totaled $2,107,000. | $2,107,000 donations and private sources | 2.1M USD |  |
| 64670634:10 | Government grants and contracts totaled $3,543,000. | $3,543,000 government grants and contracts | 3.5M USD |  |
| 64670634:11 | School public revenue and fees totaled $14,729,000. | $14,729,000 school public revenue and fees | 14.7M USD |  |
| 74fdf437:0 | 95% of Equestrian Connection clients returned for services by October  | 95% client return rate | 95% |  |
| 74fdf437:1 | Equestrian Connection raised $363,000 through the Race is On campaign  | $363,000 funds raised | 363K USD |  |
| 74fdf437:2 | 342 volunteers donated 6,180 hours to program services and events duri | 6,180 volunteer hours | 6.2K volunteer hours |  |
| 74fdf437:3 | 94% of expenses went to program services, with 4% administrative and 1 | 94% program expense ratio | 94% |  |
| 74fdf437:4 | Equestrian Connection received a $1.6 million gift for expansion. | $1,600,000 expansion gift | 1.6M USD |  |
| 74fdf437:5 | 85% of revenue came from donations and 15% from fees. | 85% donation revenue share | 85% |  |
| 74fdf437:6 | In-kind revenue totaled $66,215 and in-kind expenses were $53,297. | $66,215 in-kind revenue | 66.2K USD |  |
| e2416d20:0 | Neighborhood House served 14,884 individuals in 2017. | 14,884 individuals served | 14.9K individuals serv |  |
| e2416d20:1 | 53% of those served in 2017 are immigrants or refugees. | 53% immigrant/refugee percentage | 53% |  |
| e2416d20:2 | 2,467 people found jobs, homes, or became citizens with Neighborhood H | 2,467 people achieving key outcomes | 2.5K people achieving  |  |
| e2416d20:3 | 821 elderly and disabled clients were served in 2017. | 821 elderly/disabled clients served | 821 elderly/disabled c |  |
| e2416d20:4 | 515 struggling youth and young adults were served in 2017. | 515 youth served | 515 youth served |  |
| e2416d20:5 | 1,062 children and their families were served through Early Childhood  | 1,062 children/families in early education | 1.1K children/families |  |
| e2416d20:6 | More than 1,400 people received health services through Community Heal | 1,400 people receiving health services | 1.4K people receiving  |  |
| e2416d20:7 | 2,778 volunteers gave their time to Neighborhood House in 2017. | 2,778 volunteers | 2.8K volunteers |  |
| e2416d20:8 | 74 different languages are spoken by community members served. | 74 languages spoken | 74 languages spoken |  |
| e2416d20:9 | Total operating revenue in 2017 was $19,960,805. | $19,960,805 total operating revenue | 20M USD |  |
| e2416d20:10 | Total operating expense in 2017 was $19,775,927. | $19,775,927 total operating expense | 19.8M USD |  |
| cc947e2b:0 | Agudath Israel of America served over 10,000,000 meals during the heig | 10,000,000 meals served during COVID | 10M meals served durin |  |
| cc947e2b:1 | Agudath Israel of America secured $28,000,000 in security funding in N | $28,000,000 new jersey security funding | 28M USD |  |
| cc947e2b:2 | Agudath Israel of America secured $20,000,000 for Jewish education fun | $20,000,000 jewish education funding | 20M USD |  |
| cc947e2b:3 | Agudath Israel of America secured $30,000,000 in educational services  | $30,000,000 educational services received | 30M USD |  |
| cc947e2b:4 | Agudath Israel of America secured $11,000,000 in funding for busing 49 | $11,000,000 new jersey busing funding | 11M USD |  |
| cc947e2b:5 | Agudath Israel of America's PCS has placed 6,807 people in jobs since  | 6,807 PCS job placements | 6.8K PCS job placement |  |
| cc947e2b:6 | Agudath Israel of America's COPE has served over 6,700 students since  | 6,700 COPE students served | 6.7K COPE students ser |  |
| cc947e2b:7 | Agudath Israel of America's Pirchei Agudas Yisroel has 125 branch loca | 125 pirchei branch locations | 125 pirchei branch loc |  |
| cc947e2b:8 | Agudath Israel of America's Conference of Synagogue Rabbonim has more  | 350 conference rabbonim members | 350 conference rabboni |  |
| cc947e2b:9 | Agudath Israel of America's Ki Heim Chayeinu has 10,000 weekly partici | 10,000 ki heim chayeinu weekly participants | 10K ki heim chayeinu w |  |
| cc947e2b:12 | Agudath Israel of America's SBCO serviced 500 homeowners in the last y | 500 SBCO homeowners serviced | 500 SBCO homeowners se |  |
| cc947e2b:13 | Agudath Israel of America's Project LEARN helped 170 cases monthly fro | 170 project LEARN monthly cases | 170 project LEARN mont |  |
| cc947e2b:14 | Agudath Israel of America's Daf Yomi Commission supports 1,600 affilia | 1,600 daf yomi affiliated shiurim | 1.6K daf yomi affiliat |  |
| e136ede4:1 | The Anti-Displacement Tax Fund assisted more than 130 longtime homeown | 130 homeowners receiving ADTF assistance | 130 homeowners receivi |  |
| e136ede4:2 | 10 new single-family homeowners closed on homes through the Home on th | 10 new single-family homeowners | 10 new single-family h |  |
| e136ede4:3 | 21 high-quality multifamily units opened in English Avenue. | 21 multifamily units opened | 21 multifamily units o |  |
| e136ede4:4 | 33 rental units were initiated with the groundbreaking at 839 Joseph E | 33 rental units under construction | 33 rental units under  |  |
| e136ede4:5 | 2,270 volunteers engaged through the Volunteer Corps. | 2,270 volunteers engaged | 2.3K volunteers engage |  |
| e136ede4:6 | 6,810 volunteer hours were invested into the community. | 6,810 volunteer hours | 6.8K volunteer hours |  |
| e136ede4:7 | 262 households received Thanksgiving meal delivery. | 262 households served thanksgiving meals | 262 households served  |  |
| e136ede4:8 | 220 youth were provided for through holiday gift giving. | 220 youth served by holiday gifts | 220 youth served by ho |  |
| e136ede4:9 | The average down payment assistance for new homeowners was $42,188. | $42,188 average down payment assistance | 42.2K USD |  |
| e136ede4:10 | $125,000 was paid in assistance through the Anti-Displacement Tax Fund | $125,000 ADTF assistance paid | 125K USD |  |
| 8552b09e:0 | Families Network made more than 1,700 home visits to 82 families throu | 1,700 home visits made | 1.7K home visits made |  |
| 8552b09e:1 | 82 families were served through the Parents as Teachers and Healthy Fa | 82 families served in home visiting | 82 families served in  |  |
| 8552b09e:2 | 14 adult participants completed the Parent Leadership Training Institu | 14 PLTI graduates | 14 PLTI graduates |  |
| 8552b09e:4 | The All Pro Dads program served 84 children and 61 fathers (plus 9 mot | 84 children in all pro dads | 84 children in all pro |  |
| 8552b09e:5 | The Healthy Families program completed 707 visits and served 34 famili | 707 healthy families visits | 707 healthy families v |  |
| 8552b09e:6 | The Parents as Teachers program served 48 families and 47 children, wi | 48% parents as teachers families served | 48% |  |
| 8552b09e:7 | 20 relative childcare providers received individualized, on-site Child | 20 childcare consultations provided | 20 childcare consultat |  |
| 8552b09e:8 | Total revenues, grants, and other support for the year were $1,022,004 | $1,022,004 total revenue | 1M USD |  |
| 8552b09e:9 | Total expenses for the year were $955,236, with $837,703 going to prog | $955,236 total expenses | 955.2K USD |  |
| 8552b09e:10 | Volunteers donated at least 1,205 hours in the 2024-2025 year, valued  | $1,205 volunteer hours donated | 1.2K USD |  |
| 029625c6:0 | CAAGKC served 444,738 individuals with food and toiletries through its | 444,738 individuals served with food | 444.7K individuals ser |  |
| 029625c6:1 | The organization has been fighting poverty in Kansas City for 43 years | 43 years fighting poverty | 43 years fighting pove |  |
| 029625c6:2 | 87% of clients were below the Federal Poverty Guidelines. | 87% clients below poverty line | 87% |  |
| 029625c6:3 | Weatherization impacted 482 lives and completed 225 homes. | 482 lives impacted by weatherization | 482 lives impacted by  |  |
| 029625c6:4 | Healthy Homes served 67 households and impacted 137 lives. | 67 households served by healthy homes | 67 households served b |  |
| 029625c6:5 | Youth Services awarded 57 college scholarships. | 57 college scholarships awarded | 57 college scholarship |  |
| 029625c6:6 | Supportive Services helped 989 individuals avoid eviction. | 989 individuals avoided eviction | 989 individuals avoide |  |
| 029625c6:7 | Total revenue for the year was $8,038,211. | $8,038,211 total revenue | 8M USD |  |
| 029625c6:8 | Total expenses were $7,841,504. | $7,841,504 total expenses | 7.8M USD |  |
| 029625c6:9 | CAAGKC has 46 dedicated team members. | 46 team members | 46 team members |  |
| 3e3ff326:0 | Springboard distributed $1,227,350 in direct cash assistance to famili | $1,227,350 direct cash assistance distributed | 1.2M USD |  |
| 3e3ff326:1 | 10,078 participations in Springboard programming occurred in 2022. | 10,078 program participations | 10.1K program particip |  |
| 3e3ff326:2 | 10,088 meals, food boxes, and water cases were distributed in communit | 10,088 meals and supplies distributed | 10.1K meals and suppli |  |
| 3e3ff326:3 | 4,234 instances of staff supporting residents with housing stability w | 4,234 housing stability supports | 4.2K housing stability |  |
| 3e3ff326:4 | 5,264 items were distributed through Community Care Closets and Back t | 5,264 items distributed to residents | 5.3K items distributed |  |
| 3e3ff326:5 | 3,207 engagements with summer camps and after school programs occurred | 3,207 youth program engagements | 3.2K youth program eng |  |
| 3e3ff326:6 | 97.6% of Magnolia Mother's Trust participants felt somewhat or extreme | 97.6% participants feeling supported | 97.6% |  |
| 3e3ff326:7 | 82% of mothers felt more hopeful about their children's futures. | 82% hopeful about children's futures | 82% |  |
| 3e3ff326:8 | 79% of mothers reported feeling more hopeful about their own future. | 79% hopeful about own future | 79% |  |
| 3e3ff326:9 | 70% of mothers felt capable of caring for their own emotional, physica | 70% capable of self-care | 70% |  |
| a00adaa5:0 | Bridge of Hope surpassed its 3-year goal of a 50% increase in parents  | 70% parents and children served growth target | 70% |  |
| a00adaa5:1 | The organization served 314 parents and children in the 2020-2021 prog | 314 parents and children served | 314 parents and childr |  |
| a00adaa5:2 | 100% of families experienced caring, friendship, and helpful connectio | 100% families with positive volunteer connections | 100% |  |
| a00adaa5:3 | Bridge of Hope is expanding its financial support goal to 68% growth,  | 68% financial support growth target | 68% |  |
| f1169e89:0 | The Foundation for Enhancing Communities ended 2012 with $63,029,917 i | $63,029,917 total assets | 63M USD |  |
| f1169e89:1 | The Foundation for Enhancing Communities paid out a total of $5,097,17 | $5,097,174 total grants paid | 5.1M USD |  |
| f1169e89:2 | The Foundation for Enhancing Communities received $6 million in new co | $6,000,000 new contributions | 6M USD |  |
| f1169e89:3 | The Foundation for Enhancing Communities established 44 new funds, man | 44 new funds and agreements | 44 new funds and agree |  |
| f1169e89:4 | The Foundation for Enhancing Communities now administers over 825 fund | 825 total funds administered | 825 total funds admini |  |
| f1169e89:5 | Over 325 students were awarded nearly $600,000 in scholarships in 2012 | $325 scholarship recipients | 325 USD |  |
| f1169e89:6 | 89 students received $136,370 in matched funds through the PATH progra | $136,370 PATH matched funds | 136.4K USD |  |
| f1169e89:7 | The Foundation for Enhancing Communities received 250 grant applicatio | $262,000 discretionary grants funded | 262K USD |  |
| f1169e89:8 | The Women's Fund has granted over $46,000 since 2008. | $46,000 women's fund cumulative grants | 46K USD |  |
| f1169e89:9 | The Foundation for Enhancing Communities manages 41 charitable trusts  | $8,786,052 charitable trust assets | 8.8M USD |  |
| c31a2d63:0 | The YWRC served 1,035 clients through its Empowerment programs in FY16 | 1,035 empowerment clients served | 1K empowerment clients |  |
| c31a2d63:1 | The YWRC served 289 Young Moms clients and their children in FY16. | 289 young moms clients served | 289 young moms clients |  |
| c31a2d63:2 | 82% of Young Moms graduated high school, compared to a national averag | 82% young moms graduation rate | 82% |  |
| c31a2d63:3 | 89% of Young Moms demonstrated improved parenting skills. | 89% improved parenting skills | 89% |  |
| c31a2d63:4 | 86% of new moms initiated breastfeeding, compared to a national averag | 86% breastfeeding initiation rate | 86% |  |
| c31a2d63:5 | 91% of participants can identify the parts of the internal reproductiv | 91% reproductive health knowledge | 91% |  |
| c31a2d63:6 | 94% of participants can define self-esteem. | 94% self-esteem knowledge | 94% |  |
| c31a2d63:7 | The YWRC facilitated programs in 32 different schools throughout Great | 32 schools served | 32 schools served |  |
| c31a2d63:8 | The YWRC provided 112 Empowerment, Young Moms and therapy groups. | 112 total groups provided | 112 total groups provi |  |
| c31a2d63:9 | The Sit On It! event raised $133,000 to support girls and young women. | $133,000 sit on it! event revenue | 133K USD |  |
| c31a2d63:10 | The Celebrity Servers Night raised $64,000. | $64,000 celebrity servers night revenue | 64K USD |  |
| c31a2d63:11 | The Basket Auction raised nearly $25,000. | $25,000 basket auction revenue | 25K USD |  |
| 9b354fed:0 | The Thompson served 26,180 meals in fiscal year 2022, a significant in | 26,180 total meals served | 26.2K total meals serv |  |
| 9b354fed:1 | Home-delivered Meals on Wheels grew from just over 11,000 meals last y | 17,304 meals on wheels delivered | 17.3K meals on wheels  |  |
| 9b354fed:2 | The organization provided 953 rides to seniors in the community. | 953 rides provided | 953 rides provided |  |
| 9b354fed:3 | There were 15,000 check-ins by 1,704 individuals for education, fitnes | 15,000 program check-ins | 15K program check-ins |  |
| 9b354fed:4 | The Thompson's total operating income for fiscal year 2022 was $586,44 | $586,446 total operating income | 586.4K USD |  |
| 9b354fed:5 | Total operating expenses for fiscal year 2022 were $647,462. | $647,462 total operating expenses | 647.5K USD |  |
| 9b354fed:6 | The organization's invested funds ending balance grew to $2,214,382 as | $2,214,382 invested funds balance | 2.2M USD |  |
| ebaed191:1 | Bethesda has provided more than $13 million in charity care and financ | $13,000,000 charity care and financial assistance provided | 13M USD |  |
| ebaed191:2 | In its first year, 75% of Senior Living residents participated in Beth | 75% senior living resident wellness participation rate | 75% |  |
| ebaed191:6 | The Bethesda Health Group Foundation added 81 additional donors in 201 | 1,197 total donors | 1.2K total donors |  |
| ebaed191:7 | Bethesda received more than $700,000 through the Lasting Heritage init | $700,000 lasting heritage initiative donations | 700K USD |  |
| 5cfbbca2:0 | Since 1978, Turning Point of Lehigh Valley has worked with nearly 100, | 100,000 total individuals served since 1978 | 100K total individuals |  |
| 5cfbbca2:1 | In 2017, there were 117 people killed in Pennsylvania as a result of d | 117 domestic violence deaths in PA in 2017 | 117 domestic violence  |  |
| 5370f17e:0 | Drew CDC served 3,834 families in the Watts-Willowbrook, Compton, and  | 3,834 families served | 3.8K families served |  |
| 5370f17e:1 | 2,550 child care providers received trauma-informed care training. | 2,550 providers trained | 2.5K providers trained |  |
| 5370f17e:2 | 65% of children in the Early Childhood STEAM Education program graduat | 65% kindergarten-ready rate | 65% |  |
| 5370f17e:3 | 94% of participants in the mental health program achieved success. | 94% participant success rate | 94% |  |
| 5370f17e:5 | Staff engagement rating was 9.4 out of 10. | 9.4 staff engagement | 9.4 staff engagement |  |
| 5370f17e:6 | The Winter Wonderland event served 1,080 participants and distributed  | 1,080 winter wonderland participants | 1.1K winter wonderland |  |
| 5370f17e:7 | The median monthly household income of families served was $2,222. | $2,222 median household income | 2.2K USD |  |
| 5370f17e:8 | 79% of families cited employment as their primary child care need. | 79% child care need for employment | 79% |  |
| 5370f17e:9 | 55% of mental health program participants had a primary treatment need | 55% mental health treatment need | 55% |  |
| e83840a4:1 | 98% of Way2Work participants are employed in the competitive workforce | 98% way2work employment rate | 98% |  |
| e83840a4:2 | CCS provided housing through home supports for 35 Vermonters in FY25. | 35 vermonters housed | 35 vermonters housed |  |
| e83840a4:4 | Total support and revenue for CCS was $8,952,385. | $8,952,385 total revenue | 9M USD |  |
| e83840a4:6 | 20 students (74%) in the School2Work program were placed in jobs. | 74% school2work job placement rate | 74% |  |
| e83840a4:7 | 14 students participated in the Bridging program across 5 high schools | 14 bridging program students | 14 bridging program st |  |
| e83840a4:8 | 110 participant-led classes were held in the Peer Growth & Lifelong Le | 110 PGLL participant-led classes | 110 PGLL participant-l |  |
| e83840a4:9 | CCS performed more than 20 safety and accessibility inspections at Sha | 20 home safety inspections | 20 home safety inspect |  |
| 1c0ad786:0 | Phoenix House California served 29,748 individuals in FY 2023-2024. | 29,748 total individuals served | 29.7K total individual |  |
| 1c0ad786:1 | The organization provided treatment for 3,243 individuals, including 6 | 3,243 individuals in treatment | 3.2K individuals in tr |  |
| 1c0ad786:2 | Prevention sessions reached 24,016 youth and adults. | 24,016 prevention participants | 24K prevention partici |  |
| 1c0ad786:3 | Over 3,000 Narcan kits were distributed to the community. | 3,000 narcan kits distributed | 3K narcan kits distrib |  |
| 1c0ad786:4 | The prevention programs reached 13,550 youth and adults, including 6,7 | 13,550 prevention program reach | 13.6K prevention progr |  |
| 1c0ad786:5 | Life Skills classes were provided to 1,362 students using the Botvin L | 1,362 students in life skills classes | 1.4K students in life  |  |
| 1c0ad786:6 | Over 250 drug prevention presentations were given in schools. | 250 school presentations | 250 school presentatio |  |
| 1c0ad786:7 | In-custody substance use disorder treatment served 2,057 clients throu | 2,057 in-custody clients served | 2.1K in-custody client |  |
| 1c0ad786:8 | The organization operates in more than 70 schools in Orange County. | 70 schools served in orange county | 70 schools served in o |  |
| 1c0ad786:9 | Total expenses for FY 2023-2024 were $32,297,070. | $32,297,070 total expenses | 32.3M USD |  |
| 1c0ad786:10 | Total program expenses were $27,991,683. | $27,991,683 program expenses | 28M USD |  |
| 1c0ad786:11 | The first 5K Walk for Overdose Awareness drew more than 250 participan | 250 5K walk participants | 250 5K walk participan |  |
| 0455b54f:0 | Marguerite's Place provided 14,090 nights of housing to families in 20 | 14,090 nights of housing provided | 14.1K nights of housin |  |
| 0455b54f:1 | 75% of transitional housing families secured permanent long-term housi | 75% transitional housing success rate | 75% |  |
| 0455b54f:2 | Marguerite's Place served 23,400 healthy meals in its childcare progra | 23,400 healthy meals served in childcare | 23.4K healthy meals se |  |
| 0455b54f:3 | 73% of Child Care families were connected with additional community re | 73% child care families connected to resources | 73% |  |
| 0455b54f:4 | Marguerite's Place provided 1,560 hours of case management. | 1,560 hours of case management | 1.6K hours of case man |  |
| 7fa6354f:0 | Sixteen children graduated to kindergarten from The Children's Center  | 16 children graduated to kindergarten | 16 children graduated  |  |
| 7fa6354f:1 | The Children's Center maintained an average monthly enrollment of 176  | 176 average monthly enrollment | 176 average monthly en |  |
| 7fa6354f:2 | 100% of first and second graders in Summer Camp maintained or increase | 100% literacy skills maintained or increased | 100% |  |
| 7fa6354f:3 | 100% of kindergarteners in Summer Camp maintained or increased their m | 100% math skills maintained or increased | 100% |  |
| 7fa6354f:5 | A baby forms 700 new neural connections per second in the first years  | 700 neural connections per second | 700 neural connections |  |
| 7fa6354f:6 | Students with early education are 29% more likely to graduate from hig | 29% high school graduation likelihood increase | 29% |  |
| a5520e3e:11 | Total Unrestricted Revenues and Other Support for the year was $2,058, | $2,058,673 total unrestricted revenue | 2.1M USD |  |
| a5520e3e:12 | Total Expenses for the year were $2,058,041. | $2,058,041 total expenses | 2.1M USD |  |
| a5520e3e:13 | 87% of expenses went to Program Services. | 87% program services percentage | 87% |  |
| a5520e3e:14 | Net Assets at end of year were $1,688,423. | $1,688,423 net assets end of year | 1.7M USD |  |
| f46565eb:1 | The organization's adoption program completed over 2,400 adoptions sin | 2,400 total adoptions completed | 2.4K total adoptions c |  |
| f46565eb:2 | The number of children served by the adoption program increased by ove | 50% adoption program growth | 50% |  |
| f46565eb:3 | Family preservation services comprised over 53% of total direct expens | 53% family preservation share of expenses | 53% |  |
| f46565eb:4 | Over 200 young people participated in Summer Adventures camp programs  | 200 summer camp participants | 200 summer camp partic |  |
| f46565eb:5 | The Career Connections program served over 100 youth, with 89 successf | 89 career connections graduates | 89 career connections  |  |
| f46565eb:6 | 19 youth entered college in September 2008, all still enrolled and suc | 19 youth entering college | 19 youth entering coll |  |
| f46565eb:7 | 2,017 children were able to remain in their parents' care thanks to Or | 2,017 children kept with parents | 2K children kept with  |  |
| f46565eb:8 | 96% of families served stayed together for a year or more after servic | 96% family preservation success rate | 96% |  |
| f46565eb:9 | 398 children were supported in foster and relative homes each day. | 398 children in foster/relative care daily | 398 children in foster |  |
| f46565eb:10 | 113 children were returned to parents or relatives from foster care in | 113 children reunified with family | 113 children reunified |  |
| f46565eb:11 | 90 children found loving adoptive parents in 2008. | 90 adoptions completed in 2008 | 90 adoptions completed |  |
| f46565eb:12 | 248 children received adoption services through Orchards in 2008. | 248 children receiving adoption services | 248 children receiving |  |
| f46565eb:13 | The Michigan Department of Human Services entrusted Orchards with serv | 140 new families served via in-home interventions | 140 new families serve |  |
| f46565eb:14 | The average household income of families served was $23,000. | $23,000 average family income | 23K USD |  |
| ce7b337f:0 | HomeSafe cares for 50 percent of all children served by the five organ | 50% share of state's intensive-therapy kids served | 50% |  |
| ce7b337f:1 | 76 girls and boys ages 7-17 received therapeutic care and educational  | 76 children in residential program | 76 children in residen |  |
| ce7b337f:2 | 23 children progressed enough to step down to a lower level of care: 7 | 23 children stepped down to lower care | 23 children stepped do |  |
| ce7b337f:3 | 86% of children in care for 6+ months demonstrated improved behavior a | 86% improved behavior and functioning | 86% |  |
| ce7b337f:4 | 95% of kids were promoted to the next grade level. | 95% grade promotion rate | 95% |  |
| ce7b337f:5 | 88% of children who attended school throughout the year maintained an  | 88% school attendance rate | 88% |  |
| ce7b337f:6 | 11 young adults lived at Pond Place and all were pursuing an education | 11 young adults in independent living | 11 young adults in ind |  |
| ce7b337f:7 | 3,994 children under age 5 received free developmental and social/emot | 3,994 children under 5 assessed | 4K children under 5 as |  |
| ce7b337f:8 | 150 people impacted by domestic violence received crisis intervention  | 150 domestic violence clients served | 150 domestic violence  |  |
| ce7b337f:9 | 91% of teen and adult SafetyNet clients demonstrated increased coping  | 91% increased coping and resiliency | 91% |  |
| ce7b337f:11 | The newly constructed Sylvester Family West Campus provides a home for | 12 beds at new west campus | 12 beds at new west ca |  |
| 0405ab89:0 | Elim Park achieved a 96% apartment occupancy rate in 2023, one of the  | 96% apartment occupancy rate | 96% |  |
| 0405ab89:1 | Elim Park invested $4 million in capital improvements, including the n | $4,000,000 capital investment | 4M USD |  |
| 0405ab89:3 | Nelson Hall, a 315-seat theater, has brought joy to thousands of music | 315 theater seating capacity | 315 theater seating ca |  |
| 0405ab89:4 | Elim Park presented a $5,000 check to the Susan G. Komen Foundation du | $5,000 donation to komen foundation | 5K USD |  |
| 0405ab89:5 | Total revenues for Elim Park in 2023 were $35,622,757. | $35,622,757 total revenues | 35.6M USD |  |
| 0405ab89:6 | Total contributions to Elim Park in 2023 were $365,063. | $365,063 total contributions | 365.1K USD |  |
| 0405ab89:7 | Total assets for Elim Park in 2023 were $99,322,298. | $99,322,298 total assets | 99.3M USD |  |
| 5a23e2e5:0 | STARS placed 60 participants in competitive integrated employment at 3 | 60 participants placed in jobs | 60 participants placed |  |
| 5a23e2e5:1 | The Center Based Employment program earned $618,000 in annual revenue. | $618,000 center based employment revenue | 618K USD |  |
| 5a23e2e5:2 | STARS served 42 individuals in its Day Training for Adults program. | 42 DTA participants served | 42 DTA participants se |  |
| 5a23e2e5:3 | STARS transports drove 94,055 miles in the past year. | 94,055 transport miles driven | 94.1K transport miles  |  |
| 5a23e2e5:4 | The Center Based Employment program processed 187,125 remotes in the p | 187,125 remotes processed | 187.1K remotes process |  |
| 5a23e2e5:6 | The In-Home Services program currently serves ten families. | 10 families served in-home | 10 families served in- |  |
| e31d17df:0 | Graceworks Lutheran Services provided more than $4.9 million in uncomp | $4,915,000 uncompensated charity care | 4.9M USD |  |
| e31d17df:1 | Bethany Village is home to more than 700 seniors on a 100-acre campus. | 700 seniors served at bethany village | 700 seniors served at  |  |
| e31d17df:2 | Graceworks Enhanced Living provides home environments for more than 20 | 200 adults served in enhanced living | 200 adults served in e |  |
| e31d17df:3 | Graceworks Housing Services provides affordable apartments for more th | 750 housing services residents | 750 housing services r |  |
| e31d17df:4 | Bethany Village Home Health Care served more than 1,000 new clients in | 1,000 new home health clients | 1K new home health cli |  |
| e31d17df:5 | Graceworks Housing Services achieved a 98% occupancy rate in 2023. | 98% housing occupancy rate | 98% |  |
| e31d17df:6 | The Graceworks Women's Council handmade 850 Christmas gifts for reside | 850 handmade gifts by women's council | 850 handmade gifts by  |  |
| e31d17df:7 | Graceworks staff devoted 240 volunteer hours to community projects in  | 240 employee volunteer hours | 240 employee volunteer |  |
| e31d17df:8 | 205 employees received Stoneburner Service Awards for 10+ years of ser | 205 long-service employees recognized | 205 long-service emplo |  |
| 24e18655:0 | Island Harvest distributed 18.3 million pounds of food in fiscal year  | 18,300,000 pounds of food distributed | 18.3M pounds of food d |  |
| 24e18655:2 | Island Harvest facilitated SNAP applications generating an estimated $ | $10,000,000 SNAP benefits generated | 10M USD |  |
| 24e18655:3 | Volunteers logged 58,707 hours, equivalent to more than 28 full-time e | 58,707 volunteer hours logged | 58.7K volunteer hours  |  |
| 24e18655:4 | 62% of food distributed was rescued from waste. | 62% percentage of food rescued | 62% |  |
| 24e18655:5 | 23 graduates completed the Warehouse and Inventory Control Training Pr | 23 training program graduates | 23 training program gr |  |
| 24e18655:6 | Island Harvest conducted 2,098 nutrition education sessions. | 2,098 nutrition education sessions | 2.1K nutrition educati |  |
| 24e18655:7 | The Healthy Harvest Farm grew 60 crop varieties, yielding over 18,000  | 60 crop varieties grown | 60 crop varieties grow |  |
| 24e18655:8 | Island Harvest secured $250,000 for the new 'Nourish Suffolk County' p | $250,000 funding for nourish suffolk county | 250K USD |  |
| 24e18655:9 | Island Harvest successfully advocated for an additional $23 million in | $23,000,000 additional HPNAP funding secured | 23M USD |  |
| 24e18655:10 | Total support and revenue for fiscal year 2023-2024 was $40,176,185. | $40,176,185 total support and revenue | 40.2M USD |  |
| 24e18655:11 | Total expenses for fiscal year 2023-2024 were $40,019,175. | $40,019,175 total expenses | 40M USD |  |
| 71709748:1 | Select Human Services provided community habilitation and respite supp | 580 community habilitation/respite recipients | 580 community habilita |  |
| 71709748:2 | The Mask Avengers volunteer team produced over 10,000 face masks for r | 10,000 masks produced by volunteers | 10K masks produced by  |  |
| 71709748:4 | New Hope Community served 328 free hot meals at its 3rd Annual Communi | 328 holiday meals served | 328 holiday meals serv |  |
| 71709748:5 | New Hope Community contributed funds to provide gifts to 100 children  | 100 children gifted through head start | 100 children gifted th |  |
| 71709748:6 | Select Human Services assisted 63 individuals in self-directing their  | 63 self-direction participants | 63 self-direction part |  |
| 71709748:7 | New Hope Community launched a telemedicine program in 28 residences in | 28 residences with telemedicine | 28 residences with tel |  |
| 71709748:8 | The Hope Farm donated hundreds of pounds of fresh produce to Sullivan  | 11,097 hope farm production (pounds) | 11.1K hope farm produc |  |
| 71709748:9 | New Hope Community's Lawrence house collected and delivered 100 pounds | 100 can tabs donated (pounds) | 100 can tabs donated ( |  |
| 65c918ec:0 | Fun Time Academy served 350 students across 4 locations in the 2024-20 | 350 students served | 350 students served |  |
| 65c918ec:1 | 91% of VPK students ended the year at or above expectations. | 91% VPK students at or above expectations | 91% |  |
| 65c918ec:2 | 87% of all ages meet or exceed social-emotional milestones. | 87% children meeting social-emotional milestones | 87% |  |
| 65c918ec:3 | 84% of all ages meet or exceed all milestones assessed. | 84% children meeting all milestones | 84% |  |
| 65c918ec:4 | 95% of families are highly satisfied and would recommend Fun Time Acad | 95% family satisfaction rate | 95% |  |
| 65c918ec:5 | The 2025 gala raised over $737,542 in support of Fun Time Academy's mi | $737,542 gala funds raised | 737.5K USD |  |
| 65c918ec:6 | Fun Time Academy has grown from 12 students in 1961 to 350 students to | 350 current student enrollment | 350 current student en |  |
| 65c918ec:7 | Fun Time Academy now operates 4 locations. | 4 number of locations | 4 number of locations |  |
| 65c918ec:8 | Volunteers contributed 1,221.75 hours and raised $178,455. | $1221.75 volunteer hours | 1.2K USD |  |
| 65c918ec:10 | 30% of revenue comes from private philanthropic support (donations and | 30% revenue from philanthropy | 30% |  |
| 9fa2652e:0 | SpiriTrust Lutheran served 22,837 people in 2017. | 22,837 total people served | 22.8K total people ser |  |
| 9fa2652e:1 | SpiriTrust Lutheran provided more than $8.6 million in benevolent care | $8,600,000 benevolent care provided | 8.6M USD |  |
| 9fa2652e:2 | The VITA program returned nearly $7.3 million to York County residents | $7,300,000 VITA tax returns to community | 7.3M USD |  |
| 9fa2652e:3 | The VITA program impacted 7,295 taxpayers and dependents. | 7,295 VITA individuals impacted | 7.3K VITA individuals  |  |
| 9fa2652e:4 | 4,364 dedicated volunteers donated more than 100,000 hours of their ti | 100,000 volunteer hours donated | 100K volunteer hours d |  |
| 9fa2652e:5 | SpiriTrust Lutheran Home Care & Hospice made 206,049 visits to clients | 206,049 home care visits | 206K home care visits |  |
| 9fa2652e:6 | The Senior Companion Program served 195 clients with 57 volunteers in  | 195 senior companion clients served | 195 senior companion c |  |
| 9fa2652e:7 | SpiriTrust Lutheran welcomed 1,010 new residents to its senior living  | 1,010 new senior living residents | 1K new senior living r |  |
| 9fa2652e:8 | The Cornerstone Dinner has raised more than $2.3 million since its inc | $2,300,000 cornerstone dinner total raised | 2.3M USD |  |
| 9fa2652e:9 | Donors contributed more than $2,500,000 in 2017. | $2,500,000 donor contributions | 2.5M USD |  |
| 810829ad:0 | The Resource and Crisis Center of Galveston County answered 15,658 hot | 15,658 hotline calls answered | 15.7K hotline calls an |  |
| 810829ad:1 | The organization provided 13,693 bed nights in shelter. | 13,693 shelter bed nights | 13.7K shelter bed nigh |  |
| 810829ad:2 | Over 4,000 community members attended outreach presentations. | 4,000 outreach attendees | 4K outreach attendees |  |
| 810829ad:3 | The center provided 913 total hours of therapy. | 913 therapy hours | 913 therapy hours |  |
| 810829ad:4 | 454 victims received medical accompaniment. | 454 medical accompaniments | 454 medical accompanim |  |
| 810829ad:5 | 336 victims received legal services. | 336 legal services recipients | 336 legal services rec |  |
| 810829ad:6 | Total organizational revenue was $4,373,565.19. | $4,373,565 total revenue | 4.4M USD |  |
| 810829ad:7 | Direct victim services spending was $2,781,150.05. | $2,781,150 direct victim services spending | 2.8M USD |  |
| 5f0a0f54:0 | In 2025, Family Promise of Greater Wichita served 500 families, includ | 500 families served | 500 families served |  |
| 5f0a0f54:1 | 75% of families exiting shelter moved into stable housing. | 75% shelter exit rate to stable housing | 75% |  |
| 5f0a0f54:2 | 50 families avoided entering shelter by moving directly into housing t | 50 shelter diversions | 50 shelter diversions |  |
| 5f0a0f54:3 | The organization invested $202,000 in rental assistance to keep famili | $202,000 rental assistance invested | 202K USD |  |
| 5f0a0f54:4 | Over $600,000 in volunteer time was donated to power the organization' | $600,000 volunteer time value | 600K USD |  |
| 5f0a0f54:5 | 14 partner organizations collaborated to remove housing barriers for f | 14 partner organizations | 14 partner organizatio |  |
| 5f0a0f54:7 | The organization's total revenue for 2025 was $2,033,483. | $2,033,483 total revenue | 2M USD |  |
| 5f0a0f54:8 | Total expenses for 2025 were $1,157,377. | $1,157,377 total expenses | 1.2M USD |  |
| 0cca730e:1 | Advocates provided 4664 advocacy services including crisis interventio | 4,664 advocacy services provided | 4.7K advocacy services |  |
| 0cca730e:2 | The YWCA hosted 69 speaking engagements that educated 3085 adult and t | 3,085 community members educated | 3.1K community members |  |
| 0cca730e:4 | In 2024, the YWCA educated 391 youth in Lewiston, Clarkston, and Asoti | 391 youth educated on dating violence | 391 youth educated on  |  |
| 0cca730e:5 | The Soup-Port Our Shelters events served 962 bowls of soup and raised  | $19,750 soup event profit | 19.8K USD |  |
| 0cca730e:6 | Up to 57% of all homeless women report that domestic violence was the  | 57% homeless women citing domestic violence | 57% |  |
| 0cca730e:7 | Financial abuse is present in 98% of abusive relationships. | 98% abusive relationships with financial abuse | 98% |  |
| 0cca730e:9 | 42% of the local population is considered low to moderate income, earn | 42% low-to-moderate income population | 42% |  |
| a79f5931:0 | THRIVE distributed 915,907 pounds of food to the community. | 915,907 pounds of food distributed | 915.9K pounds of food  |  |
| a79f5931:1 | THRIVE stabilized 476 families (1,172 people) through its programs. | 476 families stabilized | 476 families stabilize |  |
| a79f5931:2 | THRIVE provided 692 financial coaching sessions through financial assi | 692 financial coaching sessions | 692 financial coaching |  |
| a79f5931:3 | THRIVE completed 1,179 home deliveries for neighbors with significant  | 1,179 home deliveries completed | 1.2K home deliveries c |  |
| a79f5931:4 | THRIVE extended Christmas assistance to 571 children. | 571 children served at christmas | 571 children served at |  |
| a79f5931:5 | THRIVE served 832 people through Project Connect. | 832 project connect participants | 832 project connect pa |  |
| a79f5931:6 | THRIVE provided 14,530+ books through its Free Bookstore. | 14,530 books provided | 14.5K books provided |  |
| a79f5931:7 | The Garden at THRIVE grew 706 pounds of food. | 706 pounds of food grown | 706 pounds of food gro |  |
| a79f5931:8 | THRIVE engaged 300 weekly volunteers, each giving two or more hours in | 300 weekly volunteers | 300 weekly volunteers |  |
| a79f5931:9 | THRIVE served 240 furloughed families and hundreds more affected by SN | 240 furloughed families served | 240 furloughed familie |  |
| a79f5931:10 | THRIVE created a permanent Hispanic Foods section, serving 35 families | 35 families served weekly by hispanic foods section | 35 families served wee |  |
| a79f5931:11 | THRIVE amplified community awareness through 19 media placements. | 19 media placements | 19 media placements |  |
| d9fef44f:0 | Facing Forward touched the lives of nearly 3,000 individuals affected  | 3,000 individuals served | 3K individuals served |  |
| d9fef44f:1 | Facing Forward's Permanent Supportive Housing program housed 638 indiv | 638 PSH individuals housed | 638 PSH individuals ho |  |
| d9fef44f:2 | 79% of clients who exited Permanent Supportive Housing moved on to oth | 79% PSH exit to permanent housing | 79% |  |
| d9fef44f:3 | 98% of Permanent Supportive Housing clients were enrolled in health in | 98% PSH health insurance enrollment | 98% |  |
| d9fef44f:4 | 88% of Permanent Supportive Housing clients received preventative care | 88% PSH preventative care rate | 88% |  |
| d9fef44f:5 | Facing Forward's Skilled Assessors completed housing assessments for 1 | 1,609 housing assessments completed | 1.6K housing assessmen |  |
| d9fef44f:6 | The Housing Location program served 30 individuals in FY2023. | 30 housing location clients | 30 housing location cl |  |
| d9fef44f:7 | The Expedited Housing Initiative served 45 households in FY2023. | 45 expedited housing households | 45 expedited housing h |  |
| d9fef44f:8 | The First Foundations program served 19 families (61 individuals) in F | 61 first foundations individuals | 61 first foundations i |  |
| d9fef44f:9 | The Home Connection program served 139 families (389 individuals) in F | 389 home connection individuals | 389 home connection in |  |
| d9fef44f:10 | Total revenue for FY2023 was $6,436,789. | $6,436,789 total revenue | 6.4M USD |  |
| f090f7af:0 | The YMCA of Greater Houston served more than 320,000 people in 2022. | 320,000 total people served | 320K total people serv |  |
| f090f7af:3 | YMCA International Services provided legal consultations to over 13,00 | 13,000 legal consultations provided | 13K legal consultation |  |
| f090f7af:4 | The YMCA served 884 unaccompanied children through its Post Release Se | 884 unaccompanied children served | 884 unaccompanied chil |  |
| f090f7af:5 | The Trafficked Persons Assistance Program helped 245 survivors of sex  | 245 trafficking survivors helped | 245 trafficking surviv |  |
| f090f7af:6 | Over 60,000 teen members found a place to belong at the YMCA. | 60,000 teen members | 60K teen members |  |
| f090f7af:7 | The YMCA distributed 2,587,770 pounds of food to families in the Houst | 2,587,770 pounds of food distributed | 2.6M pounds of food di |  |
| f090f7af:8 | Volunteers donated 394,910 hours of service to the YMCA. | 394,910 volunteer hours donated | 394.9K volunteer hours |  |
| f090f7af:9 | The YMCA served 21,866 older adults through ForeverWell programs. | 21,866 older adults served | 21.9K older adults ser |  |
| 30a05d18:0 | Jewish Family Service serves more than 100,000 individuals every year. | 100,000 individuals served annually | 100K individuals serve |  |
| 30a05d18:1 | JFS was founded in 1854 as the first charity in Los Angeles. | 1,854 year founded | 1.9K year founded |  |
| 30a05d18:2 | Total revenue for fiscal year 2015-2016 was $41,048,266. | $41,048,266 total revenue (FY2016) | 41M USD |  |
| 30a05d18:3 | Total revenue for fiscal year 2014-2015 was $34,666,199. | $34,666,199 total revenue (FY2015) | 34.7M USD |  |
| 30a05d18:4 | The 23rd Annual Gala raised over $1.1 million. | $1,100,000 gala fundraising total | 1.1M USD |  |
| 30a05d18:5 | The SOVA program distributed over 35,000 pounds of food for Thanksgivi | 35,000 thanksgiving food distributed (lbs) | 35K thanksgiving food  |  |
| 30a05d18:6 | The Laughing Matters event raised over $80,000 for domestic violence s | $80,000 funds raised for domestic violence | 80K USD |  |
| 30a05d18:7 | A Day of Hope raised over $250,000. | $250,000 day of hope fundraising | 250K USD |  |
| 30a05d18:8 | The Tools for School event provided over 250 students with backpacks a | 250 students served by tools for school | 250 students served by |  |
| 9d9ae097:0 | Irving Cares spent $820,857 on services including rent, utilities, foo | $820,857 total spending on services | 820.9K USD |  |
| 9d9ae097:2 | Irving Cares distributed 76,713 meals to Irving families. | 76,713 meals distributed | 76.7K meals distribute |  |
| 9d9ae097:3 | 199 Irving families received rent assistance totaling $223,445. | $199 families receiving rent assistance | 199 USD |  |
| 9d9ae097:4 | 364 Irving families received utility assistance totaling $152,370. | $364 families receiving utility assistance | 364 USD |  |
| 9d9ae097:5 | 480 individuals participated in 1,440 hours of financial coaching. | 480 financial coaching participants | 480 financial coaching |  |
| 9d9ae097:6 | 304 Irving families per month received nutritious groceries on average | 304 monthly grocery recipients | 304 monthly grocery re |  |
| 9d9ae097:7 | 12,628 targeted referrals were made to other resources for clients. | 12,628 referrals made | 12.6K referrals made |  |
| 9d9ae097:8 | 146 Irving households were served by Invest In Yourself / Employment S | 146 employment services households | 146 employment service |  |
| 9d9ae097:9 | 28 Irving residents completed education or GED certifications. | 28 education completions | 28 education completio |  |
| 9d9ae097:10 | 80 cents of every dollar raised goes directly to client services. | $80 cents per dollar to services | 80 USD |  |
| ee3f949b:0 | 100% of women surveyed said they know more ways to plan for their safe | 100% safety knowledge rate | 100% |  |
| ee3f949b:1 | 100% of women surveyed said they feel more hopeful about the future as | 100% hopefulness rate | 100% |  |
| ee3f949b:2 | 95% of women surveyed said they know more about community resources as | 95% resource knowledge rate | 95% |  |
| ee3f949b:3 | St. Martha's Hall provided support and information to 1,878 callers th | 1,878 hotline callers served | 1.9K hotline callers s |  |
| ee3f949b:4 | The organization provided peace, safety, and hope for 4,982 days and n | 4,982 shelter days and nights | 5K shelter days and ni |  |
| ee3f949b:6 | The organization provided continued support and resources through foll | 52 women in follow-up services | 52 women in follow-up  |  |
| ee3f949b:7 | St. Martha's Hall assisted 51 women in establishing a goal plan for th | 51 women with goal plans | 51 women with goal pla |  |
| ee3f949b:8 | The organization provided support, information, and accompaniment for  | 14 women in judicial advocacy | 14 women in judicial a |  |
| ee3f949b:9 | Total revenue for the fiscal year was $1,649,245. | $1,649,245 total revenue | 1.6M USD |  |
| 149d2e8e:1 | 100% of early childhood education children were equipped and ready for | 100% kindergarten readiness rate | 100% |  |
| 149d2e8e:2 | 95% of before and after school care and summer enrichment students gai | 95% life skills retention rate | 95% |  |
| 149d2e8e:3 | 94% of families maintained or improved their quality of life. | 94% family quality of life rate | 94% |  |
| 149d2e8e:4 | 92% of families increased knowledge of available community resources t | 92% community resource knowledge rate | 92% |  |
| 149d2e8e:5 | 88% of students managed, identified, and appropriately expressed their | 88% emotional regulation rate | 88% |  |
| 149d2e8e:6 | 82% of early childhood education children met and exceeded age-appropr | 82% milestone achievement rate | 82% |  |
| 149d2e8e:7 | UP After School serves K-6th grade students in 9 St. Louis City and Co | 9 after school program locations | 9 after school program |  |
| 8bf02961:0 | New Narrative served 2,040 participants in FY2024. | 2,040 participants served | 2K participants served |  |
| 8bf02961:1 | New Narrative increased service delivery by 35% in both clinical and h | 35% service delivery increase | 35% |  |
| 8bf02961:2 | New Narrative added 62 housing units, a 24% growth in its housing port | 62% new housing units added | 62% |  |
| 8bf02961:3 | New Narrative filled 95% of its open beds. | 95% bed occupancy rate | 95% |  |
| 8bf02961:5 | New Narrative's staff grew to 324, an 11% increase. | 324% staff count | 324% |  |
| 8bf02961:7 | 40% of staff report lived experience with mental health challenges. | 40% staff with lived experience | 40% |  |
| 8bf02961:8 | New Narrative saved 157 hours of staff documentation time through AI t | 157 documentation hours saved | 157 documentation hour |  |
| 8bf02961:9 | New Narrative's total net support and revenue was $37,853,182. | $37,853,182 total revenue | 37.9M USD |  |
| 8bf02961:10 | New Narrative's total operating expenses were $35,683,998. | $35,683,998 total expenses | 35.7M USD |  |
| 0b829d61:0 | SLI served 130 people through its residential and community living pro | 130 residential and community living clients | 130 residential and co |  |
| 0b829d61:1 | SLI's community integration and job training program served 46 people. | 46 job training program clients | 46 job training progra |  |
| 0b829d61:2 | SLI's targeted case management program served 209 people. | 209 case management clients | 209 case management cl |  |
| 0b829d61:3 | SLI operated 21 homes serving 95 men and women with 24/7 support. | 21 residential homes | 21 residential homes |  |
| 0b829d61:4 | SLI's Independent Living Program served 34 men and women. | 34 independent living clients | 34 independent living  |  |
| 0b829d61:5 | SLI has raised $1.4 million over the years through its two annual spec | $1,400,000 funds raised from events | 1.4M USD |  |
| 0b829d61:6 | SLI received a $233,363 FHLBank grant for renovations to homes serving | $233,363 fhlbank grant amount | 233.4K USD |  |
| 0b829d61:8 | SLI's vehicles travel approximately 364 trips per day transporting cli | 364 daily client trips | 364 daily client trips |  |
| c0325150:0 | The Children's Cabinet served 10,000 Nevada children and families in n | 10,000 children and families served | 10K children and famil |  |
| c0325150:1 | The organization distributed 8,044 food boxes to families. | 8,044 food boxes distributed | 8K food boxes distribu |  |
| c0325150:2 | The Children's Cabinet provided $266,324 in rental assistance to famil | $266,324 rental assistance provided | 266.3K USD |  |
| c0325150:3 | The organization delivered 3,367 free therapy sessions, saving clients | $3,367 free therapy sessions | 3.4K USD |  |
| c0325150:4 | The Children's Cabinet managed $35.8 million in federal and state coro | $35,800,000 relief funds managed | 35.8M USD |  |
| c0325150:5 | Over 20,000 clients were connected to assistance and services through  | 20,000 clients connected via outreach | 20K clients connected  |  |
| c0325150:6 | The organization distributed 22,500 PPE, curriculum kits, and books to | 22,500 PPE and kits distributed | 22.5K PPE and kits dis |  |
| c0325150:9 | 260 students participated in suicide prevention education. | 260 suicide prevention participants | 260 suicide prevention |  |
| c0325150:10 | 200 youth received computers to support distance learning. | 200 computers provided to youth | 200 computers provided |  |
| c0325150:11 | Nearly 104,000 pounds of food were delivered to 1,115 households. | 104,000 pounds of food delivered | 104K pounds of food de |  |
| c0325150:12 | The Adopt-a-Family program matched over 800 families with donors for h | 800 families adopted for holidays | 800 families adopted f |  |
| 714f6f9a:1 | 40% of youth who age out of foster care end up homeless. | 40% homelessness rate | 40% |  |
| 714f6f9a:2 | Only 25% of foster youth graduate from high school. | 25% high school graduation rate | 25% |  |
| 714f6f9a:3 | 50% of foster youth struggle with substance abuse. | 50% substance abuse rate | 50% |  |
| 714f6f9a:4 | 60% of foster youth are incarcerated by age 21. | 60% incarceration rate | 60% |  |
| 714f6f9a:5 | 222 youth participated in programs this year. | 222 youth participants | 222 youth participants |  |
| 714f6f9a:6 | 85 volunteers supported the organization. | 85 volunteers | 85 volunteers |  |
| bdad0921:0 | Los usuarios de Freedom Lifemap tenían 1,65 veces más probabilidades d | 1.65 mejora en la atención | 1.65 mejora en la aten |  |
| bdad0921:1 | Un año después del ingreso al programa, se registró un aumento del 38% | 38% aumento del bienestar general | 38% |  |
| bdad0921:2 | Un año después del ingreso al programa, se registró una reducción del  | 50% reducción de la vulnerabilidad | 50% |  |
| bdad0921:3 | La productividad de los gestores de casos aumentó un 48% en EverFree U | 48% aumento de productividad | 48% |  |
| bdad0921:4 | Freedom Lifemap generó más de $50,000 de ahorros anuales en eficiencia | $50,000 ahorros anuales en eficiencia | 50K USD |  |
| bdad0921:5 | Se logró un 65% más de logro de objetivos que la atención tradicional, | 65% logro de objetivos | 65% |  |
| bdad0921:8 | El 100% de los sobrevivientes encuestados recomendaron continuar usand | 100% recomendación de sobrevivientes | 100% |  |
| bdad0921:11 | Más de 1600 evaluaciones fueron completadas en el piloto de 7 organiza | 1,600 evaluaciones completadas | 1.6K evaluaciones comp |  |
| 38c9a81d:0 | The Financial Wellness Program served 22 participants, with 86% comple | 22% participants enrolled | 22% |  |
| 38c9a81d:1 | Participants' median Financial Capability Scale score increased by 32% | 32% FCS score increase | 32% |  |
| 38c9a81d:2 | Participants' median Financial Stress Scale score decreased by 57%, fr | 57% FSS score decrease | 57% |  |
| 38c9a81d:3 | 100% of participants who completed 6 hours of coaching had a budget an | 100% budget and savings plan | 100% |  |
| 38c9a81d:4 | Participants paid off a total of $37,283 in debt and saved a total of  | $37,283 total debt paid | 37.3K USD |  |
| 38c9a81d:5 | 94.8% of participants were satisfied with their coaching experience. | 94.8% satisfaction rate | 94.8% |  |
| 38c9a81d:6 | The Financial Coach Training Program trained 108 service providers fro | 108 service providers trained | 108 service providers  |  |
| 38c9a81d:7 | 1,457 women received financial coaching through the Financial Coach Tr | 1,457 women coached | 1.5K women coached |  |
| 38c9a81d:8 | 74% of clients in the Financial Coach Training Program increased their | 74% FCS improvement rate | 74% |  |
| 8899adf8:1 | Volunteers provided 9,320 service hours to support LISTEN's mission. | 9,320 volunteer service hours | 9.3K volunteer service |  |
| 8899adf8:2 | 60 volunteer cook teams prepared home-cooked meals for the community. | 60 volunteer cook teams | 60 volunteer cook team |  |
| d04d1475:0 | 97% of CAN students graduate from high school. | 97% high school graduation rate | 97% |  |
| d04d1475:1 | CAN students attend 19 more days of school per year compared to their  | 19 additional school days attended | 19 additional school d |  |
| d04d1475:2 | 98% of CAN elementary students maintained or improved academically ove | 98% elementary academic improvement rate | 98% |  |
| d04d1475:3 | Over 1,037,652 pounds of food were distributed, equivalent to almost 8 | 1,037,652 pounds of food distributed | 1M pounds of food dist |  |
| d04d1475:4 | Over 99% of evictions due to nonpayment of rent have been prevented si | 99% eviction prevention rate | 99% |  |
| d04d1475:5 | CAN distributed at least 1,037,652 pounds of food valued at over $2 mi | 34 food distribution increase | 34 food distribution i |  |
| d04d1475:6 | 386 unique volunteers donated 3,630 hours of their time, valued at $12 | $386 unique volunteers | 386 USD |  |
| d04d1475:7 | Total monetary donations were $316,981, including $136,598 from indivi | $316,981 total monetary donations | 317K USD |  |
| d04d1475:8 | In-kind donations had a monetary value of $2,149,785 from over 350 don | $2,149,785 in-kind donation value | 2.1M USD |  |
| d04d1475:9 | Over 80 free energy assessments were conducted and retrofits started o | 80 free energy assessments | 80 free energy assessm |  |
| 35494eab:0 | The Family Center/La Familia raised $2,366,029 to support its programm | $2,366,029 total funds raised | 2.4M USD |  |
| 35494eab:1 | The utility assistance program served 455 households. | 455 households served by utility assistance | 455 households served  |  |
| 35494eab:2 | The family development team connected over 170 households to basic hea | 170 households connected to health services | 170 households connect |  |
| 35494eab:3 | 46 families enrolled in early childhood education in 2023-2024. | 46 families enrolled in ECE | 46 families enrolled i |  |
| 35494eab:4 | 76% of families needed tuition assistance for early childhood educatio | 76% families needing tuition assistance | 76% |  |
| 35494eab:5 | 80% of families enrolled at El Nidito rely on financial assistance to  | 80% families relying on financial assistance | 80% |  |
| 35494eab:7 | 59 children were enrolled in El Nidito. | 59 children enrolled in el nidito | 59 children enrolled i |  |
| 35494eab:8 | 480 community members participated in Mi Voz meetings and classes. | 480 community members in mi voz | 480 community members  |  |
| 35494eab:9 | 16 mobile home park community leaders represented 8 parks through Mi V | 16 mobile home park leaders | 16 mobile home park le |  |
| 35494eab:10 | 12 community members completed the Family Leadership Training Institut | 12 FLTI graduates | 12 FLTI graduates |  |
| 35494eab:11 | 44 households received extensive family support services. | 44 households with extensive support | 44 households with ext |  |
| 35494eab:12 | $169,534 was awarded in utility support. | $169,534 utility support awarded | 169.5K USD |  |
| 35494eab:13 | 120 individuals enrolled in family development classes. | 120 enrolled in family development classes | 120 enrolled in family |  |
| 35494eab:14 | 366 food bags were distributed through the McBackPack Program. | 366 food bags distributed | 366 food bags distribu |  |
| 98eb1221:1 | 92 percent of students in the program remained at the same school over | 92% school stability rate | 92% |  |
| 98eb1221:2 | 100 percent of students in the program were promoted to the next grade | 100% grade promotion rate | 100% |  |
| 98eb1221:3 | More than 100,000 youth in Houston and Texas are estimated to be facin | 100,000 youth facing homelessness | 100K youth facing home |  |
| 98eb1221:9 | 423 volunteers contributed 3,310 hours to RaiseUp Families. | 423 total volunteers | 423 total volunteers |  |
| 98eb1221:10 | Volunteers contributed 3,310 hours to RaiseUp Families. | 3,310 total volunteer hours | 3.3K total volunteer h |  |
| 520af13c:0 | Raising Special Kids helped 11,053 Arizona children who have disabilit | 11,053 children served | 11.1K children served |  |
| 520af13c:1 | Over 400 families were connected with veteran mentor parents through p | 400 families matched with mentors | 400 families matched w |  |
| 520af13c:2 | 36% of families served have income at or below 80% of the area median  | 36% low-income families served | 36% |  |
| 520af13c:3 | Over 30% of the organization's support now comes directly from the com | 30% community support share | 30% |  |
| 520af13c:4 | Total revenues for 2022 were $2,314,010. | $2,314,010 total revenues | 2.3M USD |  |
| 520af13c:5 | Total expenses for 2022 were $2,060,569. | $2,060,569 total expenses | 2.1M USD |  |
| 682c5959:1 | In 2023, EFI-certified farms paid $3.7 million in worker bonuses. | $3,700,000 worker bonuses paid in 2023 | 3.7M USD |  |
| 682c5959:2 | Since 2014, EFI-certified farms have paid a total of $21.3 million in  | $21,300,000 total worker bonuses since 2014 | 21.3M USD |  |
| 682c5959:3 | Over 50,500 workers are on farms with EFI-trained leadership teams. | 50,515 workers on efi-trained farms | 50.5K workers on efi-t |  |
| 682c5959:4 | EFI completed 84 certifications and had 4 certifications in progress a | 84 certifications completed in 2023 | 84 certifications comp |  |
| 682c5959:5 | A total of 4,592 individuals were trained through EFI programs in 2023 | 4,592 individuals trained in 2023 | 4.6K individuals train |  |
| 682c5959:6 | Since its inception, 2,813 leadership team members have been trained t | 2,813 leadership team members trained (lifetime) | 2.8K leadership team m |  |
| 682c5959:7 | The Walmart Foundation provided a $2 million grant to launch and scale | $2,000,000 walmart foundation grant for ECIP | 2M USD |  |
| 682c5959:8 | EFI's total revenue and support in 2023 was $2.65 million. | $2,647,001 total revenue in 2023 | 2.6M USD |  |
| 682c5959:9 | EFI's total expenses in 2023 were $3.74 million. | $3,739,221 total expenses in 2023 | 3.7M USD |  |
| f4819890:0 | Since opening in 2014, Heroes and Horses has served 72 combat veterans | 72 total veterans served since 2014 | 72 total veterans serv |  |
| f4819890:1 | In 2017, the program served 16 veterans. | 16 veterans served in 2017 | 16 veterans served in  |  |
| f4819890:2 | 100% of graduates from the three-phase program have moved on to the ne | 100% graduates with renewed purpose | 100% |  |
| f4819890:3 | Post Phase 2, 80% of participants reported the program helped them tra | 80% participants transcending limitations | 80% |  |
| f4819890:4 | Post Phase 2, 80% of participants identified a growing sense of primar | 80% participants identifying purpose | 80% |  |
| f4819890:5 | The program received 150 applications in 2017, a record number. | 150 applications received in 2017 | 150 applications recei |  |
| f4819890:6 | Heroes and Horses adopted 15 wild mustangs from the Oregon BLM in 2017 | 15 wild mustangs adopted in 2017 | 15 wild mustangs adopt |  |
| f4819890:7 | The organization had 70 volunteers in 2017. | 70 volunteers in 2017 | 70 volunteers in 2017 |  |
| f4819890:9 | The program's Phase 1 and 2 run for 40 consecutive days, the longest v | 40 program length in days | 40 program length in d |  |
| ccb0dcad:0 | Jewish Family Service provided more than $950,000 in financial relief  | $950,000 flood financial relief | 950K USD |  |
| ccb0dcad:1 | Jewish Family Service assisted more than 500 Holocaust Survivors in th | 500 holocaust survivors served | 500 holocaust survivor |  |
| ccb0dcad:2 | The Claims Conference increased funding for JFS services for Holocaust | $1,000,000 claims conference funding increase | 1M USD |  |
| ccb0dcad:3 | Jewish Family Service provided 145 rides through its Escorted Transpor | 145 daily rides provided | 145 daily rides provid |  |
| ccb0dcad:4 | JFS School Social Workers meet with 100 students of all ages each day. | 100 students seen daily | 100 students seen dail |  |
| ccb0dcad:5 | The JFS Resource Center fields 48 new requests for information and ass | 48 daily resource requests | 48 daily resource requ |  |
| ccb0dcad:6 | JFS Geriatric Care Managers provide 55 different services to vulnerabl | 55 geriatric services offered | 55 geriatric services  |  |
| ccb0dcad:7 | The JFS Family Case Management team assists 35 families. | 35 families in case management | 35 families in case ma |  |
| ccb0dcad:8 | Clinicians in the JFS Outpatient Counseling Department meet with 45 co | 45 counseling clients | 45 counseling clients |  |
| ccb0dcad:9 | Art Van donated more than 500 pieces of furniture delivered by JFS to  | 500 furniture pieces donated | 500 furniture pieces d |  |
| ab7a5a03:0 | City House provided 9,249 days of homelessness prevention via resident | 9,249 days of homelessness prevented | 9.2K days of homelessn |  |
| ab7a5a03:1 | City House responded to 654 crisis calls. | 654 crisis calls answered | 654 crisis calls answe |  |
| ab7a5a03:2 | 90% of transitional living clients exited the program into safe and st | 90% transitional living success rate | 90% |  |
| ab7a5a03:3 | City House served 309 new and unique clients across all programs. | 309 new unique clients served | 309 new unique clients |  |
| ab7a5a03:4 | City House provided 2,753 hours of individual and family counseling. | 2,753 counseling hours provided | 2.8K counseling hours  |  |
| ab7a5a03:5 | City House distributed 797 survival kits through street outreach and t | 797 survival kits distributed | 797 survival kits dist |  |
| ab7a5a03:6 | 218 young adults were impacted through street outreach. | 218 youth reached via street outreach | 218 youth reached via  |  |
| ab7a5a03:7 | There are about 4,000 homeless youth in North Texas. | 4,000 homeless youth in north texas | 4K homeless youth in n |  |
| ab7a5a03:8 | City House operates a 48-bed agency including emergency shelter, trans | 48 total bed capacity | 48 total bed capacity |  |
| ab7a5a03:9 | Volunteers contributed 17,250 hours, providing a value of $577,703. | $17,250 volunteer hours | 17.2K USD |  |
| 36941d40:0 | CATA served 950 people with disabilities in 2023, a 25% increase over  | 950% people with disabilities served | 950% |  |
| 36941d40:1 | CATA provided 2,285 arts workshops in 2023, a 22% increase over the pr | 2285% arts workshops provided | 2285% |  |
| 36941d40:2 | CATA reached 400 youth in weekly programs in local schools, a 24% incr | 400% youth in weekly school programs | 400% |  |
| 36941d40:3 | CATA delivered 910 workshops in local schools, a 49% increase over the | 910% workshops in local schools | 910% |  |
| 36941d40:4 | CATA partnered with 52 disability agencies, residences, nursing homes, | 52 partner agencies and schools | 52 partner agencies an |  |
| 36941d40:5 | 143 CATA artists earned commissions for their artwork. | 143 artists earning commissions | 143 artists earning co |  |
| 36941d40:6 | Over 8,000 art lovers and community members visited CATA's art exhibit | 8,000 visitors to art exhibits | 8K visitors to art exh |  |
| 36941d40:7 | 1,142 community members attended CATA performances and readings. | 1,142 attendees at performances and readings | 1.1K attendees at perf |  |
| 36941d40:8 | CATA served students across 9 school districts. | 9 school districts served | 9 school districts ser |  |
| 36941d40:9 | CATA's total expenses in 2023 were $2,115,782. | $2,115,782 total expenses | 2.1M USD |  |
| 65dd3092:1 | Total revenue for 2020 was $5,217,432. | $5,217,432 total revenue | 5.2M USD |  |
| 65dd3092:2 | Total expenses for 2020 were $2,794,652. | $2,794,652 total expenses | 2.8M USD |  |
| 65dd3092:3 | Program service expenses were $2,414,530. | $2,414,530 program service expenses | 2.4M USD |  |
| 65dd3092:4 | COMMIT's online transition platform allowed services to grow by more t | 200% service growth percentage | 200% |  |
| 65dd3092:5 | 3,279 individual coaching sessions were provided. | 3,279 individual coaching sessions | 3.3K individual coachi |  |
| 65dd3092:6 | 10 Transition Mentoring Workshops were held nationally. | 10 transition mentoring workshops | 10 transition mentorin |  |
| 65dd3092:8 | 86 mentors were involved. | 86 number of mentors | 86 number of mentors |  |
| 65dd3092:9 | 75% of COMMIT participants were referred by a friend or fellow service | 75% referral rate | 75% |  |
| 65dd3092:10 | Scholarships and educational opportunities were provided to 25 transit | 25 scholarship recipients | 25 scholarship recipie |  |
| ec055971:0 | Ada S. McKinley served 6,670 people across its programs. | 6,670 total people served | 6.7K total people serv |  |
| ec055971:1 | The organization invested $38,865,878 in the community. | $38,865,878 total community investment | 38.9M USD |  |
| ec055971:5 | Head Start & Early Learning served 383 people. | 383 head start served | 383 head start served |  |
| ec055971:6 | Foster Care & Emergency Shelter served 392 people. | 392 foster care served | 392 foster care served |  |
| ec055971:7 | The total agency budget was $44,272,937. | $44,272,937 total agency budget | 44.3M USD |  |
| ec055971:8 | General and administrative costs were 12% of the budget. | 12% admin cost percentage | 12% |  |
| 617eea50:0 | Chugachmiut served the seven Native communities of the Chugach Region  | 7 communities served | 7 communities served |  |
| 617eea50:1 | Total revenues for FY 2007 were $15,540,841. | $15,540,841 total revenues | 15.5M USD |  |
| 617eea50:2 | Total expenditures for FY 2007 were $15,471,452. | $15,471,452 total expenditures | 15.5M USD |  |
| 617eea50:5 | Chugachmiut administers HeadStart in three communities. | 3 headstart communities | 3 headstart communitie |  |
| 617eea50:6 | The Behavioral Health Department provides direct services to five regi | 5 behavioral health communities served | 5 behavioral health co |  |
| 617eea50:7 | Chugachmiut facilitated two wild land firefighting crews operating in  | 2 firefighting crews | 2 firefighting crews |  |
| 8b8cd8e1:0 | During the height of the COVID-19 pandemic, CUMAC served 38,392 client | 38392% clients served during pandemic | 38392% |  |
| 8b8cd8e1:1 | CUMAC's total revenue in FY2020 was $5,293,336. | $5,293,336 total annual revenue | 5.3M USD |  |
| 8b8cd8e1:2 | Over 70% of CUMAC's 25 team members are from the communities they serv | 70% staff from served communities | 70% |  |
| 8b8cd8e1:3 | 744 volunteers completed over 5,000 hours of volunteering. | 744 volunteers engaged | 744 volunteers engaged |  |
| bb8cb998:0 | CTN empowered 3,000 individuals to reach their destination and connect | 3,000 individuals served | 3K individuals served |  |
| bb8cb998:1 | CTN served 714 seniors and people with disabilities to access health c | 714 seniors and disabled served | 714 seniors and disabl |  |
| bb8cb998:2 | 16% of medical services riders have been riding for 5 years or more. | 16% long-term riders | 16% |  |
| bb8cb998:3 | Field trips and shuttles increased by 16% over the prior year. | 16% field trip increase | 16% |  |
| bb8cb998:4 | 87% of expenses are program related. | 87% program expense ratio | 87% |  |
| 82d11125:0 | Bayview's social accountability program generated $1,182,585 in total  | $1,182,585 total community impact | 1.2M USD |  |
| 82d11125:1 | Bayview Manor Foundation provided $491,524 in support to Bayview and i | $491,524 foundation support provided | 491.5K USD |  |
| 82d11125:2 | The Savoy gala raised $50,000 in 2019, far exceeding its $39,000 goal. | $50,000 savoy gala fundraising | 50K USD |  |
| 82d11125:3 | A 103-year-old resident broke the Guinness World Record for oldest tan | 103 oldest skydiver age | 103 oldest skydiver ag |  |
| 82d11125:5 | Bayview Manor Foundation subsidized housing for 6 independent and assi | 6 residents subsidized by foundation | 6 residents subsidized |  |
| 82d11125:6 | The Holiday Bazaar raised $3,800 for Medic One Foundation, helping tra | $3,800 holiday bazaar proceeds | 3.8K USD |  |
| 82d11125:7 | Bayview's total assets were $92,353,739 in 2019. | $92,353,739 total assets | 92.4M USD |  |
| 5b8eae73:0 | Southern Oregon Friends of Hospice provided end-of-life care to reside | $35 care fund residents served | 35 USD |  |
| 5b8eae73:1 | The organization's Hospice Unique Boutique (The HUB) generated $602,26 | $602,263 HUB gross sales | 602.3K USD |  |
| 5b8eae73:2 | The HUB received 148,000 donated items in 2023. | 148,000 items donated to HUB | 148K items donated to  |  |
| 5b8eae73:3 | Volunteers contributed 5,600 hours to The HUB in 2023. | 5,600 HUB volunteer hours | 5.6K HUB volunteer hou |  |
| 5b8eae73:5 | The HUB generated $83,018 in event sales in 2023. | $83,018 HUB event sales | 83K USD |  |
| 5b8eae73:6 | Southern Oregon Friends of Hospice relies on fundraising to fill a 30% | 30% budget gap from fundraising | 30% |  |
| 5b8eae73:7 | The organization has over 160 dedicated volunteers supporting Celia's  | 160 total volunteers | 160 total volunteers |  |
| 5b8eae73:8 | The Spiritual Care Program at Celia's House has a team of 12 spiritual | 12 spiritual care volunteers | 12 spiritual care volu |  |
| 370cd021:0 | The Home Access Program budget grew from $100,000 to $600,000 annually | $600,000 home access program budget | 600K USD |  |
| 370cd021:7 | The Blue Cross Blue Shield settlement created a $169 million health fo | $169,000,000 BCBS settlement value | 169M USD |  |
| c1a9f80b:0 | Since its founding in 2012, Western Pennsylvania Diaper Bank has distr | 4,590,000 total diapers distributed since 2012 | 4.6M total diapers dis |  |
| c1a9f80b:1 | In 2021, the organization saved families over $654,000. | $654,000 dollars saved for families in 2021 | 654K USD |  |
| c1a9f80b:2 | The Diaper Bank supports over 12,000 families annually across three co | 12,000 families served annually | 12K families served an |  |
| c1a9f80b:3 | In 2021, the organization distributed 1.9 million diapers. | 1,900,000 diapers distributed in 2021 | 1.9M diapers distribut |  |
| c1a9f80b:4 | The organization distributed 174,330 period products in 2021. | 174,330 period products distributed in 2021 | 174.3K period products |  |
| c1a9f80b:5 | The organization distributed 38,000 incontinence supplies in 2021. | 38,000 incontinence supplies distributed in 2021 | 38K incontinence suppl |  |
| c1a9f80b:7 | The organization serves three counties: Allegheny, Fayette, and Westmo | 3 counties served | 3 counties served |  |
| c1a9f80b:8 | 264 volunteers logged 1,604 hours of time in 2021. | 1,604 volunteer hours in 2021 | 1.6K volunteer hours i |  |
| c1a9f80b:9 | 302 individual donors gave $83,950 in support in 2021. | $83,950 individual donations in 2021 | 84K USD |  |
| 128de607:0 | The Society of St. Vincent de Paul provided $2,400,818 in total assist | $2,400,818 total assistance provided | 2.4M USD |  |
| 128de607:1 | Over 300 uninsured patients received life-saving medication, including | 300 patients receiving medication | 300 patients receiving |  |
| 128de607:2 | The organization served 685 families per week on average through its d | 685 families served weekly | 685 families served we |  |
| 128de607:4 | Clients received 100 vouchers for free clothing, beds, and furniture. | 100 vouchers for goods | 100 vouchers for goods |  |
| 128de607:5 | The food pantry distributed 60 diapers per family per visit, spending  | $60 diapers per family | 60 USD |  |
| 128de607:6 | Total revenue for the organization in 2020 was $13,022,953. | $13,022,953 total revenue | 13M USD |  |
| 128de607:7 | Total expenses for the organization in 2020 were $12,125,087. | $12,125,087 total expenses | 12.1M USD |  |
| 128de607:8 | The organization vaccinated more than 30 clients with COVID-19 vaccine | 30 COVID-19 vaccinations | 30 COVID-19 vaccinatio |  |
| 98dd921d:0 | LIFE CIL impacted 2,649 lives from July 1, 2020 to June 30, 2021. | 2,649 lives impacted | 2.6K lives impacted |  |
| 98dd921d:1 | 160 individuals received extended individual services this year. | 160 extended services recipients | 160 extended services  |  |
| 98dd921d:2 | Eight individuals transitioned out of a nursing home into their own ho | 8 nursing home transitions | 8 nursing home transit |  |
| 98dd921d:3 | Nearly 1,300 assistive devices were loaned from the Equipment Loan Clo | 1,300 assistive devices loaned | 1.3K assistive devices |  |
| 98dd921d:4 | LIFE CIL implemented a 'Teens in Transition' curriculum in four high s | 4 high schools with transition curriculum | 4 high schools with tr |  |
| 98dd921d:5 | Total expenses for the fiscal year were $839,049. | $839,049 total expenses | 839K USD |  |
| 98dd921d:6 | Income over expenses was $11,641. | $11,641 net income | 11.6K USD |  |
| f1189246:0 | Chrysalis served individuals with serious mental illness in Dane Count | $385,039 total expenses | 385K USD |  |
| f1189246:4 | Total revenue for Chrysalis was $395,605. | $395,605 total revenue | 395.6K USD |  |
| f1189246:5 | Medicaid revenue was $190,197. | $190,197 medicaid revenue | 190.2K USD |  |
| f1189246:6 | Department of Vocational Rehabilitation revenue was $111,192. | $111,192 DVR revenue | 111.2K USD |  |
| f1189246:7 | Community Shares of Wisconsin revenue was $72,850. | $72,850 community shares revenue | 72.8K USD |  |
| f1189246:8 | Fundraising and donations revenue was $10,763. | $10,763 fundraising and donations | 10.8K USD |  |
| f1189246:9 | Salaries and wages expenses were $284,484. | $284,484 salaries and wages | 284.5K USD |  |
| f1189246:10 | Employee benefits expenses were $25,579. | $25,579 employee benefits | 25.6K USD |  |
| f1189246:11 | Program expenses were $38,859. | $38,859 program expenses | 38.9K USD |  |
| f1189246:12 | Facility expenses were $33,098. | $33,098 facility expenses | 33.1K USD |  |
| 8eb7da05:0 | HomeSafe raised $10.3 million toward its $15 million Healing the Hurt  | $10,300,000 campaign funds raised | 10.3M USD |  |
| 8eb7da05:1 | HomeSafe serves 50 percent of all children in Florida's specialized th | 50% share of state therapeutic care | 50% |  |
| 8eb7da05:2 | 82 girls and boys ages 7-17 received therapeutic care in four group ho | 82 children in residential care | 82 children in residen |  |
| 8eb7da05:3 | 29 children progressed to a lower level of care, including 9 reunified | 29 children stepped down in care | 29 children stepped do |  |
| 8eb7da05:4 | 83% of children maintained at least 80% school attendance, and 100% we | 83% school attendance rate | 83% |  |
| 8eb7da05:5 | 81% of children in care for 6+ months demonstrated improved behavior a | 81% improved behavior rate | 81% |  |
| 8eb7da05:6 | 15,015 mothers were screened for postpartum depression and received ch | 15,015 mothers screened | 15K mothers screened |  |
| 8eb7da05:7 | 3,834 families received an initial intake or assessment for a child un | 3,834 families enrolled in early services | 3.8K families enrolled |  |
| 8eb7da05:8 | 168 people impacted by domestic violence enrolled in SafetyNet crisis  | 168 domestic violence clients served | 168 domestic violence  |  |
| 8eb7da05:9 | 85% of domestic violence clients demonstrated increased knowledge abou | 85% safety knowledge increase | 85% |  |
| 8eb7da05:10 | 13 young adults ages 18-23 lived at Pond Place, all pursuing a college | 13 young adults in independent living | 13 young adults in ind |  |
| 8eb7da05:11 | HomeSafe raised over $400,000 through its Classic Rock & Roll fundrais | $400,000 fundraiser revenue | 400K USD |  |
| 81480a77:0 | Circle of Care served 523 foster children and 253 foster families in 2 | 523 foster children served | 523 foster children se |  |
| 81480a77:1 | Foster families provided 85,630 days of care to children in 2021. | 85,630 days of foster care | 85.6K days of foster c |  |
| 81480a77:2 | 206 children obtained stable placements through foster care in 2021. | 206 stable placements | 206 stable placements |  |
| 81480a77:3 | 34 adoptions were finalized from foster care in 2021. | 34 adoptions | 34 adoptions |  |
| 81480a77:4 | 71 new foster homes were certified in 2021. | 71 new foster homes certified | 71 new foster homes ce |  |
| 81480a77:5 | Pearl's Hope served 7 mothers and 13 children in 2021. | 7 pearl's hope mothers served | 7 pearl's hope mothers |  |
| 81480a77:6 | 50% of Pearl's Hope clients obtained stable housing in 2021. | 50% pearl's hope stable housing rate | 50% |  |
| 81480a77:7 | 50% of Pearl's Hope clients obtained stable employment or income in 20 | 50% pearl's hope employment rate | 50% |  |
| 81480a77:8 | PAL program served 8 former foster youths in 2021. | 8 PAL youths served | 8 PAL youths served |  |
| 81480a77:9 | 100% of PAL clients obtained vital documents and reliable transportati | 100% PAL vital documents rate | 100% |  |
| 81480a77:10 | Counseling services served 103 total clients in 2021. | 103 counseling clients served | 103 counseling clients |  |
| 81480a77:11 | Total revenues and other support for 2021 were $5,904,280. | $5,904,280 total revenues | 5.9M USD |  |
| 81480a77:12 | Total expenses for 2021 were $4,646,116. | $4,646,116 total expenses | 4.6M USD |  |
| 81480a77:13 | Total net assets at end of 2021 were $41,405,020. | $41,405,020 total net assets | 41.4M USD |  |
| 011c1177:1 | Total revenue and support for the fiscal year was $7,056,005. | $7,056,005 total revenue | 7.1M USD |  |
| 011c1177:2 | Program services expenses totaled $4,454,276. | $4,454,276 program services expenses | 4.5M USD |  |
| 011c1177:3 | Summer camp served over 100 children, teens, and young adults. | 100 summer camp participants | 100 summer camp partic |  |
| 011c1177:4 | The Annual Fundraiser raised over $58,000 in one night. | $58,000 annual fundraiser revenue | 58K USD |  |
| 011c1177:5 | Net assets increased by $1,535,886 from the beginning to the end of th | $1,535,886 increase in net assets | 1.5M USD |  |
| 011c1177:7 | The organization provided over 100 hours of volunteer service with the | 100 volunteer service hours | 100 volunteer service  |  |
| 011c1177:8 | The 2025 Golf Scramble had 25 teams participating, its best year ever. | 25 golf scramble teams | 25 golf scramble teams |  |
| 5e0246ca:0 | AGE of Central Texas served more than 3,000 individuals annually throu | 3,000 individuals served annually | 3K individuals served  |  |
| 5e0246ca:1 | 851 caregivers received daily respite, utilized support groups, and re | 851 caregivers served | 851 caregivers served |  |
| 5e0246ca:2 | 47.7% of older adults and family caregivers served by AGE were from no | 47.7% low-income households served | 47.7% |  |
| 5e0246ca:3 | 43.9% of older adults and family caregivers served by AGE identify as  | 43.9% people of color served | 43.9% |  |
| 4ec0d342:0 | Step Up expanded services to Pasadena by housing 13 individuals in sca | 13 new housing units in pasadena | 13 new housing units i |  |
| 4ec0d342:1 | Step Up operates nine locations across Santa Monica, Hollywood, West L | 9 total program locations | 9 total program locati |  |
| 4ec0d342:2 | Eight clients moved into community employment through Step Up's vocati | 8 clients placed in jobs | 8 clients placed in jo |  |
| 4ec0d342:3 | Every person served contributes up to 30% of their income from employm | 30% income contribution rate | 30% |  |
| 519266c2:0 | CPWD assisted people with disabilities in the greater Boulder-Denver a | 5 core services offered | 5 core services offere |  |
| 519266c2:7 | Total support and revenue for CPWD was $2,658,887. | $2,658,887 total revenue | 2.7M USD |  |
| 519266c2:8 | Total expenses for CPWD were $2,452,583. | $2,452,583 total expenses | 2.5M USD |  |
| 71d70742:0 | 91% of responding caregivers were female. | 91% female caregivers | 91% |  |
| 71d70742:1 | 76% of respondents have been providing care for five or more years. | 76% long-term caregivers | 76% |  |
| 71d70742:2 | 75% of caregivers reported being tired/worn out a lot of the time. | 75% caregiver exhaustion | 75% |  |
| 71d70742:3 | 75% of caregivers reported decreased social life. | 75% decreased social life | 75% |  |
| 71d70742:4 | 55% of caregivers never ask for help for themselves. | 55% never ask for help | 55% |  |
| 71d70742:5 | 53% of employed caregivers decreased work hours due to caregiving. | 53% decreased work hours | 53% |  |
| 71d70742:6 | 40% of employed caregivers quit a job due to caregiving. | 40% quit job due to caregiving | 40% |  |
| 71d70742:7 | 72% of respondents had taken respite. | 72% used respite | 72% |  |
| 71d70742:8 | 82% of respite users felt less overwhelmed. | 82% respite reduces overwhelm | 82% |  |
| 71d70742:9 | 75% of care recipients had less social interaction due to COVID-19. | 75% reduced social interaction | 75% |  |
| 95f2054c:0 | Community Options employs over 5,500 people to support persons with di | 5,500 employees | 5.5K employees |  |
| 95f2054c:1 | Community Options supports over 1,500 residents in over 600 homes nati | 1,500 residents served | 1.5K residents served |  |
| 95f2054c:2 | Community Options supports over 1,100 individuals who are employed. | 1,100 individuals employed | 1.1K individuals emplo |  |
| 95f2054c:3 | Community Options has over 600 well-appointed homes nationwide. | 600 homes operated | 600 homes operated |  |
| 95f2054c:7 | Total expenses for the year ended June 30, 2019 were $215,586,568. | $215,586,568 total expenses | 215.6M USD |  |
| 95f2054c:8 | Net assets at the end of the year were $34,461,815. | $34,461,815 net assets | 34.5M USD |  |
| be50ade6:0 | 92% of Friends-Boston alumni graduated from high school, compared to 6 | 92% high school graduation rate | 92% |  |
| be50ade6:1 | 99% of Friends-Boston alumni are employed or pursuing secondary educat | 99% employment or higher education rate | 99% |  |
| be50ade6:2 | 154 Achievers received one-on-one Professional Mentorship. | 154 achievers served | 154 achievers served |  |
| be50ade6:3 | 452 caregivers and siblings received holistic family support. | 452 family members supported | 452 family members sup |  |
| be50ade6:4 | $85,000 was distributed in support resources to families. | $85,000 family support funds distributed | 85K USD |  |
| be50ade6:5 | 100% of caregivers say they would recommend the Friends-Boston program | 100% caregiver recommendation rate | 100% |  |
| be50ade6:6 | 86% of Achievers say they feel hopeful about their future. | 86% achiever hopefulness | 86% |  |
| be50ade6:7 | 100% of Achievers say that Friends-Boston helps them learn new things  | 100% achievers reporting learning growth | 100% |  |
| be50ade6:8 | 45 family crisis situations were addressed through 1:1 in-depth suppor | 45 family crises addressed | 45 family crises addre |  |
| 7e9ebeba:0 | St. Vincent de Paul Society of Lane County saw a 57 percent increase i | 57 increase in new donors | 57 increase in new don |  |
| 7e9ebeba:1 | St. Vincent de Paul Society of Lane County saw an 85 percent increase  | 85 increase in repeat donors | 85 increase in repeat  |  |
| 7e9ebeba:3 | St. Vincent de Paul Society of Lane County's First Place Kids program  | 273 children served by first place kids | 273 children served by |  |
| 7e9ebeba:4 | St. Vincent de Paul Society of Lane County's First Place Kids program  | 24 teens served by youth house | 24 teens served by you |  |
| 7e9ebeba:5 | St. Vincent de Paul Society of Lane County enabled 22 families to part | 22 families in homeownership program | 22 families in homeown |  |
| 7e9ebeba:6 | St. Vincent de Paul Society of Lane County opened two emergency shelte | 2 emergency shelters opened in 48 hours | 2 emergency shelters o |  |
| 7e9ebeba:7 | St. Vincent de Paul Society of Lane County's First Place Annex Night S | 20 families capacity at annex shelter | 20 families capacity a |  |
| 7e9ebeba:8 | St. Vincent de Paul Society of Lane County's First Place Annex Night S | 85 individual capacity at annex shelter | 85 individual capacity |  |
| 7e9ebeba:9 | St. Vincent de Paul Society of Lane County gained 43 new Monthly Susta | 43 new sustainer members | 43 new sustainer membe |  |
| 759c9a94:0 | His Grace Foundation provided physical, emotional, and financial suppo | 15 inpatient rooms served | 15 inpatient rooms ser |  |
| 759c9a94:1 | The 21st Annual Charity Golf Tournament raised $100,000 for patients a | $100,000 golf tournament funds raised | 100K USD |  |
| 759c9a94:2 | His Grace Foundation served 132 families through shopping list fulfill | 132 families served with shopping | 132 families served wi |  |
| 759c9a94:3 | His Grace Foundation served 2832 meals to patients, families, and care | 2,832 meals served | 2.8K meals served |  |
| 759c9a94:4 | His Grace Foundation provided 46 financial assistance gifts through th | 46 financial assistance gifts | 46 financial assistanc |  |
| 759c9a94:5 | His Grace Foundation housed 10 families of transplant patients at His  | 10 families housed | 10 families housed |  |
| 759c9a94:6 | His Grace Foundation had 41 active volunteers on the unit and 1510 tot | 1,510 volunteer hours | 1.5K volunteer hours |  |
| 759c9a94:7 | His Grace Foundation held 22 celebrations and special events on the Bo | 22 celebrations and events | 22 celebrations and ev |  |
| 759c9a94:8 | Total public support and revenue for His Grace Foundation was $529,890 | $529,890 total public support and revenue | 529.9K USD |  |
| 759c9a94:9 | Total expenses for His Grace Foundation were $504,503, with 77% going  | 77 program expense percentage | 77 program expense per |  |
| 88bfa5f4:0 | IAVA reached more than 88 million people in 2017 through media and pub | 88,000,000 people reached | 88M people reached |  |
| 88bfa5f4:1 | IAVA passed and defended policies impacting all 3 million post-9/11 ve | 3,000,000 post-9/11 veterans impacted | 3M post-9/11 veterans  |  |
| 88bfa5f4:2 | IAVA provided high-touch transition assistance to over 900 veterans. | 900 veterans receiving transition assistance | 900 veterans receiving |  |
| 88bfa5f4:3 | IAVA connected veterans through more than 302 VetTogether community-bu | 302 vettogether events | 302 vettogether events |  |
| 88bfa5f4:4 | 1,700 veterans used IAVA's online GI Bill Calculator. | 1,700 GI bill calculator users | 1.7K GI bill calculato |  |
| 88bfa5f4:5 | IAVA fought for 42 bills supporting veterans and won passage of 14. | 14 bills passed | 14 bills passed |  |
| 88bfa5f4:6 | IAVA's She Who Borne the Battle campaign reached more than 10 million  | 10,000,000 campaign reach | 10M campaign reach |  |
| 88bfa5f4:7 | IAVA raised over $5.3 million in 2017. | $5,300,000 total funds raised | 5.3M USD |  |
| 88bfa5f4:8 | IAVA contributed to or was featured in 449 original media articles. | 449 media features | 449 media features |  |
| 88bfa5f4:9 | More than 4,300 IAVA members completed the 8th Annual Member Survey. | 4,300 survey respondents | 4.3K survey respondent |  |
| a17a2fc3:0 | Average monthly enrollment in Early Head Start and Head Start Preschoo | 100% average monthly enrollment rate | 100% |  |
| a17a2fc3:1 | 85% of children transitioning to kindergarten met or exceeded the Scho | 85% kindergarten readiness rate | 85% |  |
| a17a2fc3:2 | 86% of children on an Individual Education Plan (IEP) transitioning to | 86% IEP kindergarten readiness rate | 86% |  |
| a17a2fc3:3 | From fall to spring, the 3-year-old average COR score showed a gain of | 53.55 3-year-old COR score gain | 53.55 3-year-old COR s |  |
| a17a2fc3:4 | From fall to spring, the 4-year-old average COR score showed a gain of | 43.37 4-year-old COR score gain | 43.37 4-year-old COR s |  |
| a17a2fc3:5 | 100% of Head Start Preschool children had insurance by the end of the  | 100% head start preschool insurance rate | 100% |  |
| a17a2fc3:6 | 98% of Head Start Preschool children had a dental home by the end of t | 98% head start preschool dental home rate | 98% |  |
| a17a2fc3:7 | 99% of Early Head Start children had insurance by the end of the progr | 99% early head start insurance rate | 99% |  |
| a17a2fc3:8 | 89% of Early Head Start children were up to date on immunizations by t | 89% early head start immunization rate | 89% |  |
| a17a2fc3:9 | 82 children screened positively for some level of food insecurity. | 82 children with food insecurity | 82 children with food  |  |
| a17a2fc3:10 | Staff completed 2,339.25 hours of training from August 31, 2024 to Aug | 2339.25 staff training hours | 2.3K staff training ho |  |
| a17a2fc3:11 | The average number of training hours per staff member was 55.7. | 55.7 average training hours per staff | 55.7 average training  |  |
| d2e6b6ff:0 | Turning Point of Lehigh Valley served over 3,770 people, including 444 | 3,770 total people served | 3.8K total people serv |  |
| d2e6b6ff:1 | Over 200 survivors found safe, stable housing through the progressive  | 200 survivors housed | 200 survivors housed |  |
| d2e6b6ff:2 | Advocacy services supported more than 3,900 individuals. | 3,900 individuals supported by advocacy | 3.9K individuals suppo |  |
| d2e6b6ff:3 | 63 adults and 39 children spent 24,889 nights in their own homes throu | 24,889 nights in own homes (new beginnings) | 24.9K nights in own ho |  |
| d2e6b6ff:4 | 109 adults and 116 children received 11,832 nights of safety in the Sa | 11,832 nights of safety (safe house) | 11.8K nights of safety |  |
| d2e6b6ff:5 | 13,600 hours of counseling services were provided, including individua | 13,600 hours of counseling services | 13.6K hours of counsel |  |
| d2e6b6ff:6 | 2,970 safety plans were made. | 2,970 safety plans made | 3K safety plans made |  |
| d2e6b6ff:7 | 3,420 referrals were made to outside agencies for 1,886 people. | 3,420 referrals made | 3.4K referrals made |  |
| d2e6b6ff:8 | 2,053 clients were assisted by legal advocates in civil and criminal c | 2,053 clients assisted by legal advocates | 2.1K clients assisted  |  |
| d2e6b6ff:9 | 1,284 survivors were granted protective orders as a direct result of T | 1,284 protective orders granted | 1.3K protective orders |  |
| d2e6b6ff:10 | Over 200 students graduated from the Spaces and I AM HER programs. | 200 students graduated from prevention programs | 200 students graduated |  |
| d2e6b6ff:11 | Total revenue for 2024-2025 was $3,962,762. | $3,962,762 total revenue | 4M USD |  |
| d2e6b6ff:12 | For every $1 raised, $0.75 goes directly to free, survivor-centered se | 0.75 dollars to services per $1 raised | 0.75 dollars to servic |  |
| 3f5bf745:0 | Interface Children & Family Services serves 59,000 local clients every | 59,000 annual clients served | 59K annual clients ser |  |
| 3f5bf745:1 | Interface connects 150,000 clients per year to health and human servic | 150,000 2-1-1 clients connected | 150K 2-1-1 clients con |  |
| 3f5bf745:2 | Students at 140 school campuses received on-site Mental Health Service | 140 school campuses served | 140 school campuses se |  |
| 3f5bf745:3 | 10,080 people in 63 schools and youth clubs participated in trainings  | 10,080 training participants | 10.1K training partici |  |
| 3f5bf745:4 | Provided 1,370 nights of safe shelter to victims fleeing domestic viol | 1,370 nights of shelter provided | 1.4K nights of shelter |  |
| 3f5bf745:5 | Sheltered 92 homeless youth with 83% reunited with family or offered s | 92% homeless youth sheltered | 92% |  |
| 3f5bf745:6 | Provided individual, family and group therapy to 1,181 children and fa | 1,181 therapy recipients | 1.2K therapy recipient |  |
| 3f5bf745:7 | 483 families participated in Interface's Positive Parenting Program (T | 483 families in triple p | 483 families in triple |  |
| 3f5bf745:8 | Provided intervention with 96 gang-affiliated youth in Oxnard. | 96 gang-affiliated youth served | 96 gang-affiliated you |  |
| 3f5bf745:9 | Coordinated treatment and reduced crime for more than 300 incarcerated | 300 incarcerated adults served | 300 incarcerated adult |  |
| 3f5bf745:10 | Provided immediate crisis support to 700 callers in life-threatening d | 700 crisis callers supported | 700 crisis callers sup |  |
| 3f5bf745:11 | 11,193 unique visitors used new guided website search. | 11,193 website search users | 11.2K website search u |  |
| 10c67887:0 | The Mockingbird Youth Network engaged 358 youth and alumni of foster c | 358 youth network participants | 358 youth network part |  |
| 10c67887:1 | 92% of Youth Network participants reported feeling empowered after att | 92% empowerment rate at youth advocacy day | 92% |  |
| 10c67887:2 | 92% of youth/alumni reported an increased sense of connection with the | 92% connection rate at leadership summit | 92% |  |
| 10c67887:3 | The Mockingbird Times featured 135 youth-generated articles and was di | 22,000 monthly mockingbird times subscribers | 22K monthly mockingbir |  |
| 10c67887:4 | To provide safe, independent housing for alumni of care, $1.8 million  | $1,800,000 housing funding secured | 1.8M USD |  |
| 10c67887:5 | To protect outreach services for homeless and runaway youth, $1.7 mill | $1,700,000 street youth program funding secured | 1.7M USD |  |
| 10c67887:6 | To provide safe housing and advance educational attainment, all youth  | 21 maximum age to remain in care | 21 maximum age to rema |  |
| 10c67887:7 | Over the past seven years, 17 MFM constellations have been established | 17 MFM constellations established | 17 MFM constellations  |  |
| 10c67887:8 | Total revenue for 2011 was $919,624. | $919,624 total revenue | 919.6K USD |  |
| 10c67887:9 | Total expenses for 2011 were $1,917,531. | $1,917,531 total expenses | 1.9M USD |  |
| ff8c3ea8:0 | Go Kids received a contract monitoring audit from the California Depar | 1% audit error rate | 1% |  |
| ff8c3ea8:1 | Go Kids conducted more than 1,250 screenings using ASQ and other devel | 1,250 developmental screenings | 1.2K developmental scr |  |
| ff8c3ea8:2 | Go Kids provided mental health services to 55 individuals. | 55 mental health clients | 55 mental health clien |  |
| ff8c3ea8:3 | Go Kids facilitated home visiting to 80 clients. | 80 home visiting clients | 80 home visiting clien |  |
| ff8c3ea8:4 | Go Kids engaged 32 parents in Parent Dialogue Groups using the Circle  | 32 parents in dialogue groups | 32 parents in dialogue |  |
| ff8c3ea8:5 | Go Kids provided care coordination services to 155 children and parent | 155 care coordination clients | 155 care coordination  |  |
| ff8c3ea8:6 | Go Kids offered more than 65 trainings to the child care community. | 65 trainings offered | 65 trainings offered |  |
| ff8c3ea8:7 | Go Kids received donations from more than 75 people totaling over $33, | $33,000 secret santa donations | 33K USD |  |
| ff8c3ea8:8 | Go Kids received a grant for $149,000 to provide Parents as Teachers H | $149,000 calworks home visit grant | 149K USD |  |
| ff8c3ea8:9 | Go Kids was awarded over $800,000 in additional Alternative Payment co | $800,000 alternative payment funds | 800K USD |  |
| ff8c3ea8:10 | Go Kids' total revenue for 2019 was $20,279,812. | $20,279,812 total revenue | 20.3M USD |  |
| ff8c3ea8:11 | Go Kids' total expenses for 2019 were $20,460,761. | $20,460,761 total expenses | 20.5M USD |  |
| fb8f56bd:0 | New Futures served 273 Scholars in 2024, the largest cohort in its his | 273 scholars served in 2024 | 273 scholars served in |  |
| fb8f56bd:1 | The organization celebrated 55 graduates in 2024, a record number. | 55 graduates in 2024 | 55 graduates in 2024 |  |
| fb8f56bd:2 | New Futures achieved a 90% success rate for Scholar graduation and per | 90% success rate | 90% |  |
| fb8f56bd:3 | The organization has served 813 Scholars since its founding in 1999. | 813 all-time scholars served | 813 all-time scholars  |  |
| fb8f56bd:4 | New Futures awarded $483,000 in scholarships in 2024. | $483,000 scholarships awarded | 483K USD |  |
| fb8f56bd:5 | The organization received a $2 million grant from MacKenzie Scott's Yi | $2,000,000 largest gift in history | 2M USD |  |
| fb8f56bd:6 | New Futures received a $1.5 million grant from the A. James & Alice B. | $1,500,000 clark foundation grant | 1.5M USD |  |
| fb8f56bd:7 | Total revenue and support for 2024 was $3,646,926. | $3,646,926 total revenue and support | 3.6M USD |  |
| fb8f56bd:8 | Total expenses for 2024 were $2,517,685. | $2,517,685 total expenses | 2.5M USD |  |
| fb8f56bd:9 | Net assets at the end of 2024 were $2,675,886. | $2,675,886 net assets | 2.7M USD |  |
| fb8f56bd:10 | Graduation rates for Black Scholars rose from 47.73% (2015-2016 cohort | 79.41% black scholar graduation rate | 79.41% |  |
| fb8f56bd:11 | In Spring 2024, 66% of Scholars said they had a peer in New Futures th | 85% scholars with peer support | 85% |  |
| fb8f56bd:12 | 90% of Scholars report their advisor is one of the first people they t | 90% scholars trusting advisor | 90% |  |
| dddcd7b6:0 | CAO of Scioto County served 773 adults and youth through workforce ser | 773 workforce services participants | 773 workforce services |  |
| dddcd7b6:1 | The Fatherhood program served 133% more fathers, increased categorical | 133% fatherhood program growth | 133% |  |
| dddcd7b6:2 | CAO provided 150,101 summer meals at 26 open feeding sites. | 150,101 summer meals served | 150.1K summer meals se |  |
| dddcd7b6:3 | The Senior Nutrition Program delivered 75,915 home-delivered meals and | 75,915 home-delivered meals | 75.9K home-delivered m |  |
| dddcd7b6:4 | CAO completed 6,285 applications for PIPP, Summer and Winter Crisis pr | 6,285 crisis applications completed | 6.3K crisis applicatio |  |
| dddcd7b6:5 | The organization served 3,053 households through the Home Energy Assis | 3,053 HEAP households served | 3.1K HEAP households s |  |
| dddcd7b6:6 | CAO served 1,612 women, infants, and children through the Scioto Count | 1,612 WIC caseload | 1.6K WIC caseload |  |
| dddcd7b6:7 | CAO completed 6,149 home visits, including 1,427 for Head Start and 4, | 6,149 home visits completed | 6.1K home visits compl |  |
| dddcd7b6:8 | CAO invested $6 million in the local community. | $6,000,000 community investment | 6M USD |  |
| dddcd7b6:9 | The Mobile Hygiene Unit provided 501 total visits for shower and laund | 501 mobile hygiene visits | 501 mobile hygiene vis |  |
| a1072081:0 | Aldersly served a mean of 70.5 total residents during the fiscal year. | 70.5 mean total residents | 70.5 mean total reside |  |
| a1072081:1 | Aldersly served a mean of 45.5 continuing care residents during the fi | 45.5 mean continuing care residents | 45.5 mean continuing c |  |
| a1072081:2 | Total operating expenses for the year ended September 30, 2025, amount | $10,940,392 total operating expenses | 10.9M USD |  |
| a1072081:3 | Aldersly maintained a debt service reserve of $3,214,407. | $3,214,407 debt service reserve | 3.2M USD |  |
| a1072081:4 | Aldersly maintained an operating expense reserve of $611,408. | $611,408 operating expense reserve | 611.4K USD |  |
| a1072081:5 | Aldersly has $5,685,623 in cash and cash equivalents available to fulf | $5,685,623 cash and equivalents available | 5.7M USD |  |
| a1072081:6 | Aldersly has $16,159,958 in reserves held by the bond trustee. | $16,159,958 bond trustee reserves | 16.2M USD |  |
| a1072081:7 | Per capita cost of operations was $155,183 per resident. | $155,183 per capita operating cost | 155.2K USD |  |
| 60e7220f:0 | Bethesda Lutheran Services serves more than 1,000 children, youth, and | 1,000 daily clients served | 1K daily clients serve |  |
| 60e7220f:1 | The organization expanded its Alternative Education for Disruptive You | 10 erie school districts served by AEDY | 10 erie school distric |  |
| 60e7220f:2 | The Truancy Program expanded into every school district in Erie County | 2 erie school districts not in truancy program | 2 erie school district |  |
| 60e7220f:3 | Bethesda is now licensed for two Psychiatric Residential Treatment Fac | 2 PRTF licenses in meadville | 2 PRTF licenses in mea |  |
| 60e7220f:7 | The Bethesda team expanded by over 75 people in 2020. | 75 new staff hired | 75 new staff hired |  |
| 60e7220f:8 | Student Assistance Program (SAP) liaisons assessed and provided servic | 389 SAP youth served | 389 SAP youth served |  |
| 60e7220f:9 | Total fundraising in fiscal year 2019 was $381,146. | $381,146 total fundraising | 381.1K USD |  |
| ee9087ae:0 | Cimarron Public Transit System provided safe and reliable shared-ride  | 5,238 service area square miles | 5.2K service area squa |  |
| ee9087ae:1 | United Community Action Program operates 16 Head Start/Early Head Star | 16 head start centers | 16 head start centers |  |
| ee9087ae:2 | The UCAP Housing program has 44 single-family and other units housing  | 44 housing units operated | 44 housing units opera |  |
| ee9087ae:4 | CPTS maintains a fleet of 66 vehicles providing open-to-all public tra | 66 transit fleet vehicles | 66 transit fleet vehic |  |
| ee9087ae:5 | CPTS received 10 new ADA-compliant 7-passenger vans in March 2024 to m | 10 new ADA vans received | 10 new ADA vans receiv |  |
| 2ec17b37:2 | 84% of individuals served in 2025 received help related to assault and | 84% crime types served | 84% |  |
| c6d70581:0 | The organization distributed 16,825,965 pounds of food across a seven- | 16,825,965 pounds of food distributed | 16.8M pounds of food d |  |
| c6d70581:1 | Total revenue including food donations reached $59,930,680. | $59,930,680 total revenue with food | 59.9M USD |  |
| c6d70581:3 | The ASPIRE program provided employment support services to 84 individu | 84 ASPIRE employment support recipients | 84 ASPIRE employment s |  |
| c6d70581:5 | 127 families received assistance to avoid eviction and maintain housin | 127 families avoiding eviction | 127 families avoiding  |  |
| c6d70581:6 | 49 individuals obtained employment or better employment through ASPIRE | 49 individuals gaining employment | 49 individuals gaining |  |
| c6d70581:7 | Total expenses excluding salaries and in-kind were $15,057,522. | $15,057,522 total expenses (excl. salaries/in-kind) | 15.1M USD |  |
| 9ba0a1b0:0 | Covenant House Missouri supported 234 youth in need of services during | 234 youth served | 234 youth served |  |
| 9ba0a1b0:1 | Youth received a total of 12,237 nights of shelter. | 12,237 nights of shelter | 12.2K nights of shelte |  |
| 9ba0a1b0:2 | Black youth represented 72% of the young people at Covenant House Miss | 72% black youth served | 72% |  |
| 9ba0a1b0:3 | The FY20 Executive Sleep Out raised over $385,000. | $385,000 sleep out funds raised | 385K USD |  |
| 9ba0a1b0:4 | Covenant House Missouri expanded the age range of youth eligible for s | 24 maximum age for services | 24 maximum age for ser |  |
| 9ba0a1b0:5 | The organization increased the total number of youth supported each ni | 36 nightly capacity | 36 nightly capacity |  |
| 9ba0a1b0:6 | 27,000 meals were served to youth. | 27,000 meals served | 27K meals served |  |
| 9ba0a1b0:7 | 122 youth were active in mental health services. | 122 youth in mental health services | 122 youth in mental he |  |
| ecd677ef:0 | Catholic Charities Fort Worth moved more than 4,200 families onto a me | 4,200 families moved out of poverty | 4.2K families moved ou |  |
| ecd677ef:1 | The organization served tens of thousands of clients across 28 countie | 28 counties served | 28 counties served |  |
| ecd677ef:2 | 1,453 refugees from 31 countries were served by Refugee Services. | 1,453 refugees served | 1.5K refugees served |  |
| ecd677ef:3 | 198 students earned an education credential on the Education Pathway. | 198 students earned credential | 198 students earned cr |  |
| ecd677ef:4 | On average, clients on the Financial Resiliency Pathway increased thei | $2,227 average savings increase | 2.2K USD |  |
| ecd677ef:5 | The average client on the Financial Resiliency Pathway had $542 more t | $542 monthly spending increase | 542 USD |  |
| ecd677ef:6 | 600 clients were served by refugee-focused employment services. | 600 refugee employment clients | 600 refugee employment |  |
| ecd677ef:7 | 155 mothers were served by the Gabriel Project. | 155 gabriel project mothers served | 155 gabriel project mo |  |
| ecd677ef:8 | Total revenue for 2022 was $33,101,866. | $33,101,866 total revenue | 33.1M USD |  |
| ecd677ef:9 | Total program expenses for 2022 were $32,891,564. | $32,891,564 total program expenses | 32.9M USD |  |
| fa5c5fe6:0 | Abbott House served 77 girls in FY19, with 35 new enrollments. | 77 girls served annually | 77 girls served annual |  |
| fa5c5fe6:1 | Abbott House provided a safe place for 68 youth who completed treatmen | 68 youth in safe place program | 68 youth in safe place |  |
| fa5c5fe6:2 | Abbott House served 8 youth in its independent living program for youn | 8 youth in independent living | 8 youth in independent |  |
| fa5c5fe6:3 | Abbott House's budgeted operating income was $5,975,231. | $5,975,231 budgeted operating income | 6M USD |  |
| fa5c5fe6:4 | Abbott House's budgeted expenses were $5,949,062. | $5,949,062 budgeted expenses | 5.9M USD |  |
| 779d9438:0 | 92% of survivors who had access to an advocate found them helpful. | 92% advocate helpfulness rate | 92% |  |
| 779d9438:1 | 76% of white and Black/African American female respondents felt safe i | 76% safety perception (white/black women) | 76% |  |
| 779d9438:2 | 77% of non-Black women of color respondents felt safe in the court sys | 77% safety perception (non-black women of color) | 77% |  |
| 779d9438:3 | 59% of respondents said they were criticized by judges or magistrates. | 59% criticism by judges/magistrates | 59% |  |
| 779d9438:4 | 62% of respondents interacted with the court for their most recent dom | 62% court interaction rate | 62% |  |
| 779d9438:5 | 85% of survivors with an advocate received information about additiona | 85% legal options information rate | 85% |  |
| 779d9438:6 | 76% of survivors with an advocate received resources and support for h | 76% resource and support receipt rate | 76% |  |
| 779d9438:7 | Only 59% of respondents who filed criminal charges received help from  | 59% prosecutor advocate help rate | 59% |  |
| 779d9438:8 | 12% of respondents felt pressure from the court advocate or prosecutor | 12% pressure from court personnel | 12% |  |
| 779d9438:9 | 14% of survivors with a prior protection order were criticized by cour | 14% criticism for seeking protection order | 14% |  |
| 779d9438:10 | 39% of survivors with a prior protection order were criticized by pros | 39% criticism by prosecutors | 39% |  |
| 779d9438:11 | 25% of respondents said they were criticized by advocates. | 25% criticism by advocates | 25% |  |
| 779d9438:12 | LGBTQ+ respondents reported having a court advocate 10% less often tha | 10% advocate access disparity (LGBTQ+) | 10% |  |
| 0c2bea9f:0 | Pikes Peak Hospice & Palliative Care served over 2,200 hospice and pal | 2,243 total patients served | 2.2K total patients se |  |
| 0c2bea9f:1 | The organization provided 85,504 days of care to hospice patients in 2 | 85,504 hospice days of care | 85.5K hospice days of  |  |
| 0c2bea9f:2 | Pikes Peak Hospice Foundation provided nearly $250,000 in charitable c | $250,000 charitable care funding | 250K USD |  |
| 0c2bea9f:3 | Over 1,000 adults and children received bereavement services in 2024. | 1,000 bereavement service recipients | 1K bereavement service |  |
| 0c2bea9f:4 | PPHPC launched an $8.5 million initiative to build the region's only f | $8,500,000 inpatient care center campaign | 8.5M USD |  |
| 0c2bea9f:5 | The new Inpatient Care Center will expand capacity from 8 to 12 privat | 12 new inpatient suites | 12 new inpatient suite |  |
| 0c2bea9f:6 | PPHPC served 21 percent more patients in 2024 than the prior year. | 21% patient growth rate | 21% |  |
| 0c2bea9f:9 | The Illuminations breakfast netted nearly $45,000 for programs in 2024 | $45,000 illuminations event net revenue | 45K USD |  |
| 8b5543a9:0 | Exceptional Persons, Inc. served individuals and families across multi | $20,557,363 total revenue | 20.6M USD |  |
| 8b5543a9:1 | The organization's largest program area, Community Services - Resident | 14,043,490 residential services spending | 14M residential servic |  |
| 8b5543a9:2 | Children & Family Services was the second-largest program area, with $ | 3,390,580 children & family services spending | 3.4M children & family |  |
| 8b5543a9:3 | Net administration costs were 10.3% of total expenses, indicating effi | 10.3% administrative overhead rate | 10.3% |  |
| 8b5543a9:4 | State and federal grants provided the majority of funding at $15,331,4 | 15,331,448 state and federal grants | 15.3M state and federa |  |
| 18bb8c0f:0 | CPWD served 536 intake consumers receiving services in the last fiscal | 536 consumers served | 536 consumers served |  |
| 18bb8c0f:1 | CPWD provided 20,437 service hours to consumers. | 20,437 service hours provided | 20.4K service hours pr |  |
| 18bb8c0f:2 | CPWD served consumers across 49 cities. | 49 cities served | 49 cities served |  |
| 18bb8c0f:3 | CPWD served consumers across 17 counties. | 17 counties served | 17 counties served |  |
| 18bb8c0f:4 | CPWD received 916 information and referral calls. | 916 information and referral calls | 916 information and re |  |
| 18bb8c0f:5 | 606 seniors attended low-vision peer support groups. | 606 seniors in low-vision groups | 606 seniors in low-vis |  |
| 18bb8c0f:6 | Consumers identified and started 576 goals. | 576 goals identified and started | 576 goals identified a |  |
| 18bb8c0f:7 | Consumers accomplished 130 goals. | 130 goals accomplished | 130 goals accomplished |  |
| 18bb8c0f:8 | CPWD achieved 5 job placements for consumers. | 5 job placements | 5 job placements |  |
| 18bb8c0f:9 | 3 people transitioned from nursing homes back into the community. | 3 nursing home transitions | 3 nursing home transit |  |
| 18bb8c0f:10 | The average age of CPWD consumers was 70. | 70 average consumer age | 70 average consumer ag |  |
| 18bb8c0f:11 | CPWD reported $2,418,238 in income and $2,348,899 in expenses. | $2,418,238 total income | 2.4M USD |  |
| 18588fca:1 | The organization served 285 children in the reporting period. | 285 children served | 285 children served |  |
| 18588fca:2 | 64 children returned home from foster care. | 64 children returned home | 64 children returned h |  |
| 18588fca:3 | 22 children were adopted. | 22 children adopted | 22 children adopted |  |
| 18588fca:4 | 38 new foster homes were opened. | 38 new foster homes | 38 new foster homes |  |
| 18588fca:5 | 139 total foster homes were active at year-end. | 139 total foster homes | 139 total foster homes |  |
| 18588fca:6 | 26,592 bed nights of care were provided. | 26,592 bed nights provided | 26.6K bed nights provi |  |
| 18588fca:7 | 96 independent living youth were served. | 96 IL youth served | 96 IL youth served |  |
| 18588fca:8 | 38 Bridges youth were served. | 38 bridges youth served | 38 bridges youth serve |  |
| 18588fca:9 | Total revenue was $15,983,165. | $15,983,165 total revenue | 16M USD |  |
| 18588fca:10 | Total expenses were $15,876,035. | $15,876,035 total expenses | 15.9M USD |  |
| 18588fca:11 | 85% of expenses went to program services. | 85% program expense ratio | 85% |  |
| 287485c1:0 | Family Services Center served 945 new clients in FY 2022-23. | 945 new clients served | 945 new clients served |  |
| 287485c1:1 | Family Services Center provided 4,425 total units of service in FY 202 | 4,425 total units of service | 4.4K total units of se |  |
| 287485c1:2 | Family Services Center was awarded 20 grants totaling $872,971 in FY 2 | $872,971 total grant funding | 873K USD |  |
| 287485c1:4 | Family Services Center constructed 1 home through its housing program. | 1 homes constructed | 1 homes constructed |  |
| 287485c1:5 | Family Services Center served 174 new clients through Housing Counseli | 174 housing counseling new clients | 174 housing counseling |  |
| 287485c1:6 | Family Services Center served 128 new clients through PTSA (Drug/Alcoh | 128 PTSA new clients | 128 PTSA new clients |  |
| 287485c1:7 | Family Services Center served 94 new clients through DVIP (Domestic Vi | 94 DVIP new clients | 94 DVIP new clients |  |
| 287485c1:8 | Family Services Center served 89 new clients through FAST. | 89 FAST new clients | 89 FAST new clients |  |
| 287485c1:9 | Family Services Center served 74 new clients through Counseling. | 74 counseling new clients | 74 counseling new clie |  |
| 287485c1:10 | Family Services Center served 48 new clients through PTIP (Theft) prog | 48 PTIP new clients | 48 PTIP new clients |  |
| 287485c1:11 | Family Services Center served 41 new clients through LIFT. | 41 LIFT new clients | 41 LIFT new clients |  |
| 287485c1:12 | Family Services Center served 35 new clients through Down Payment Assi | 35 DPA new clients | 35 DPA new clients |  |
| 287485c1:13 | Family Services Center served 30 new clients through AMP. | 30 AMP new clients | 30 AMP new clients |  |
| 287485c1:14 | Family Services Center served 29 new clients through WDCRP. | 29 WDCRP new clients | 29 WDCRP new clients |  |
| 287485c1:15 | Family Services Center served 19 new clients through HSP. | 19 HSP new clients | 19 HSP new clients |  |
| 287485c1:16 | Family Services Center served 18 new clients through Workforce Develop | 18 workforce development new clients | 18 workforce developme |  |
| 287485c1:17 | Family Services Center served 17 new clients through Parenting program | 17 parenting new clients | 17 parenting new clien |  |
| 287485c1:18 | Family Services Center served 5 new clients through SAP. | 5 SAP new clients | 5 SAP new clients |  |
| 287485c1:19 | Family Services Center served 3 new clients through Conflict Resolutio | 3 conflict resolution new clients | 3 conflict resolution  |  |
| 287485c1:20 | Family Services Center served 1 new client through Caring Cars. | 1 caring cars new clients | 1 caring cars new clie |  |
| 287485c1:21 | Family Services Center served 1 new client through Nurturing Fathers. | 1 nurturing fathers new clients | 1 nurturing fathers ne |  |
| 287485c1:22 | Family Services Center served 1 new client through SBG. | 1 SBG new clients | 1 SBG new clients |  |
| 287485c1:23 | Family Services Center served 138 new clients through COVID/ERAP (ende | 138 COVID/ERAP new clients | 138 COVID/ERAP new cli |  |
| 1a70e980:0 | In 2025, Skookum Kids cared for over 6 kids through its foster family  | 6 kids in foster family programs | 6 kids in foster famil |  |
| 1a70e980:1 | Over 63 volunteers provided nights of kids sleeping safe and sound. | 63 volunteers providing safe nights | 63 volunteers providin |  |
| 1b29d1a0:0 | Boulder Housing Partners superó las 2,000 unidades de vivienda asequib | 2,000 total de unidades de vivienda | 2K total de unidades d |  |
| 1b29d1a0:1 | BHP atiende a 2,924 hogares en toda la comunidad de Boulder. | 2,924 hogares atendidos | 2.9K hogares atendidos |  |
| 1b29d1a0:3 | La tasa de ocupación de BHP se mantuvo en un 95.1%. | 95.1% tasa de ocupación | 95.1% |  |
| 1b29d1a0:6 | El nuevo programa HIPPY de BHP atiende a 22 familias con niños de 2 a  | 22 familias en programa HIPPY | 22 familias en program |  |
| 1b29d1a0:7 | Iris Bistro en Golden West sirvió más de 4,429 comidas en 2025, con un | 4,429 comidas servidas en iris bistro | 4.4K comidas servidas  |  |
| 1b29d1a0:8 | BHP distribuyó 24,751 libras de alimentos a través de sus programas co | 24,751 alimentos distribuidos (libras) | 24.8K alimentos distri |  |
| 1b29d1a0:9 | BHP gestiona aproximadamente el 5% de las viviendas de Boulder, pero g | 3% porcentaje de emisiones de la ciudad | 3% |  |