#!/usr/bin/env python
# -*- coding: utf8 -*-

import os
import re
import requests
from bs4 import BeautifulSoup
import numpy as np
import pandas as pd
from tqdm import tqdm
import json


class NhanesScraper:
    """
    Class to scrape and store variable codebooks from NHANES website
    https://wwwn.cdc.gov/nchs/nhanes/default.aspx

    Parameters
    ----------
    output_fname : str
        Path to output .csv
    components : list or None, default None
        List of components, e.g. ["Demographics", "Questionnaire"]
    years : list or None, default None
        List of years, e.g. [2003, 2005]

    Attributes
    ----------
    survey_components : list
        List of components, default is ["Demographics", "Questionnaire",
        "Examination", "Laboratory", "Dietary"]
    survey_years : list
        List of years, defailt is [1999, ..., 2019]
    survey_suffix : dict
        Dictionary years to suffix, e.g {1999: "_A", 2001: "_B", ...}
    categ_dict : dict
        Dictionary category code to category name
    var_dict : dict
        Dictionary variable code to code, name, category, and codebook

    Example
    -------
    >>> import pynhanes
    >>> pynhanes.scraper.NhanesScraper("./CSV/nhanes_codebook.csv")

    """
    def __init__(self, output_fname, components=None, years=None):
        survey_components = ["Demographics", "Mortality", "Questionnaire"]
        survey_components += ["Examination", "Laboratory", "Dietary"]
        survey_years = [2000 + i for i in range(-1,20)[::2]]
        self.survey_components = components if components is not None else survey_components
        self.survey_years = years if years is not None else survey_years
        self.survey_suffix = {y: f"_{chr(66 + (y - 2000) // 2)}" for y in self.survey_years}
        self.component_dict = {"Mortality": ["Mortality Linked Data"]}
        self.categ_dict = {}
        self.var_dict = {}
        # Start scraping
        rsession = requests.Session()
        for component in self.survey_components:
            if component != "Mortality":
                for year in self.survey_years:
                    self._scrape_doc_files(component, year, rsession)
        self._add_mortality_data()
        self._add_occupation_data()
        self._codebook_cleanup()
        self._to_csv(output_fname)

        
    def _scrape_doc_files(self, component, year, rsession):
        """
        Scrape category list from component/year page on NHANES website

        Parameters
        ----------
        component : str
            Components, e.g. "Demographics"
        year : int
            Year, e.g. 2003
        rsession : requests.Session() object
            Requsts session object

        """
        print(f"Scraping ({component}, {year})...")
        component_categs = []
        if component in self.component_dict:
            component_categs = self.component_dict[component]
        doc_urls = []
        url = "https://wwwn.cdc.gov/nchs/nhanes/search/datapage.aspx?" \
              "Component={}&CycleBeginYear={}".format(component, year)
        soup = BeautifulSoup(rsession.get(url).content, features="lxml")
        rows = soup.find_all("tr")[1:]
        for row in rows:
            categ_name = row.find("td").text.strip()
            categ_code = row.find("a").text.replace(" Doc", "")
            categ_code = categ_code.removesuffix(self.survey_suffix[year])
            categ_url = "https://wwwn.cdc.gov/" + row.find("a")["href"]
            if categ_url != "#":
                doc_urls.append(categ_url)
                if categ_code not in self.categ_dict:
                    self.categ_dict[categ_code] = categ_name
                if categ_name not in component_categs:
                    component_categs.append(categ_name)
        self.component_dict[component] = component_categs
        for doc_url in tqdm(doc_urls):
            try:
                self._scrape_variables(doc_url, rsession)
            except:
                pass
        return


    def _scrape_variables(self, doc_url, rsession):
        """
        Scrape variable list from category page on NHANES website

        Parameters
        ----------
        doc_url : str
            Web address of category variables description
        rsession : requests.Session() object
            Requsts session object

        """
        year = int(doc_url.split("/")[-3].split("-")[0])
        category = doc_url.split("/")[-1].split(".")[0]
        category = category.removesuffix(self.survey_suffix[year])
        
        soup = BeautifulSoup(rsession.get(doc_url).content, features="lxml")
        variables = soup.find_all("div", attrs={"class": "pagebreak"})
        for variable in variables:
            if variable.find("table"):
                var_categ = f"{category}"
                var_code = variable.find_all("dd")[0].text.strip().upper()
                var_name = variable.find_all("dd")[1].text.strip()
                codebook = {}
                rows = variable.find("tbody").find_all("tr")
                for row in rows:
                    num_code = row.find_all("td")[0].text
                    descript = row.find_all("td")[1].text
                    descript = " ".join([d.strip() for d in descript.splitlines()])
                    codebook[num_code] = descript
                if var_code in self.var_dict:
                    codebook.update(self.var_dict[var_code]["Codebook"])
                    var_categ = self.var_dict[var_code]["Category"] + "," + var_categ
                var_data = {"Code": var_code,
                            "Name": var_name,
                            "Category": var_categ,
                            "Codebook": codebook}
                self.var_dict[var_code] = var_data
        return


    def _add_mortality_data(self):
        """
        Add mortality linked data, see description url:
        https://www.cdc.gov/nchs/data-linkage/mortality-public.htm

        """
        code_dict = {
            "ELIGSTAT": [
                "Eligibility Status for Mortality Follow-up",
                {"1": "Eligible", "2": "Under age 18, not available for public release", "3": "Ineligible"}
            ],
            "MORTSTAT": [
                "Final Mortality Status",
                {"0": "Assumed alive", "1": "Assumed deceased", ".": "Ineligible or under age 18"}
            ],
            "UCOD_LEADING": [
                "Underlying Leading Cause of Death",
                {"1": "Diseases of heart", "2": "Malignant neoplasms", "3": "Chronic lower respiratory diseases", 
                 "4": "Accidents", "5": "Cerebrovascular diseases", "6": "Alzheimer's disease ", 
                 "7": "Diabetes mellitus", "8": "Influenza and pneumonia", "9": "Nephritis, nephrotic syndrome and nephrosis", 
                 "10": "All other causes ", ".": "Missing"}
            ],
            "DIABETES": [
                "Diabetes Flag from Multiple Cause of Death (MCOD)",
                {"0": "No - Condition not listed as a multiple cause of death",
                 "1": "Yes - Condition listed as a multiple cause of death",
                 ".": "ssumed alive, under age 18, ineligible for mortality follow-up, or MCOD not available"}
            ],
            "HYPERTEN": [
                "Hypertension Flag from Multiple Cause of Death (MCOD)",
                {"0": "No - Condition not listed as a multiple cause of death",
                 "1": "Yes - Condition listed as a multiple cause of death",
                 ".": "ssumed alive, under age 18, ineligible for mortality follow-up, or MCOD not available"}
            ],
            "DODQTR": [
                "Quarter of Death: NHIS only",
                {"1": "January-March", "2": "April-June", "3": "July-September", 
                 "4": "October-December", ".": "Ineligible, under age 18, or assumed alive"}
            ],
            "DODYEAR": [
                "Year of Death: NHIS only",
                {"1986-2015": "Year", ".": "Ineligible, under age 18, or assumed alive"}
            ], 
            "WGT_NEW": [
                "Weight Adjusted for Ineligible Respondents - Person-level Sample Weight",
                {"55-50599": "Range of Values", ".": "Missing"}
            ],
            "SA_WGT_NEW": [
                "Weight Adjusted for Ineligible Respondents - Sample Adult Sample Weigh",
                {"62-239765": "Range of Values", ".": "Missing"}
            ],
            "PERMTH_INT": [
                "Number of Person Months of Follow-up from NHANES interview date",
                {"0-326": "Months", ".": "Ineligible or under age 18"}
            ],
            "PERMTH_EXM": [
                "Number of Person Months of Follow-up from NHANES Mobile Examination Center (MEC) date",
                {"0-326": "Months", ".": "Ineligible or under age 18"}
            ],
        }
        self.categ_dict["MORT"] = "Mortality Linked Data"
        for code in code_dict:
            self.var_dict[code] = {
                "Code": code,
                "Name": code_dict[code][0],
                "Category": "MORT",
                "Codebook": code_dict[code][1]
            }
        return


    def _add_occupation_data(self):
        """
        Add occupation group data, see description url:
        https://wwwn.cdc.gov/Nchs/Nhanes/2003-2004/OCQ_C.htm#Appendix_A

        """
        code_dict = {
            "1": "Executive, administrators, and managers",
            "2": "Management related occupations",
            "3": "Engineers, architects and scientists",
            "4": "Health diagnosing, assessing and treating occupations",
            "5": "Teachers",
            "6": "Writers, artists, entertainers, and athletes",
            "7": "Other professional specialty occupations",
            "8": "Technicians and related support occupations",
            "9": "Supervisors and proprietors, sales occupations",
            "10": "Sales representatives, finance, business, & commodities ex. retail",
            "11": "Sales workers, retail and personal services",
            "12": "Secretaries, stenographers, and typists",
            "13": "Information clerks",
            "14": "Records processing occupations",
            "15": "Material recording, scheduling, and distributing clerks",
            "16": "Miscellaneous administrative support occupations",
            "17": "Private household occupations",
            "18": "Protective service occupations",
            "19": "Waiters and waitresses",
            "20": "Cooks",
            "21": "Miscellaneous food preparation and service occupations",
            "22": "Health service occupations",
            "23": "Cleaning and building service occupations",
            "24": "Personal service occupations",
            "25": "Farm operators, managers, and supervisors",
            "26": "Farm and nursery workers",
            "27": "Related agricultural, forestry, and fishing occupations",
            "28": "Vehicle and mobile equipment mechanics and repairers",
            "29": "Other mechanics and repairers",
            "30": "Construction trades",
            "31": "Extractive and precision production occupations",
            "32": "Textile, apparel, and furnishings machine operators",
            "33": "Machine operators, assorted materials",
            "34": "Fabricators, assemblers, inspectors, and samplers",
            "35": "Motor vehicle operators",
            "36": "Other transportation and material moving occupations",
            "37": "Construction laborers",
            "38": "Laborers, except construction",
            "39": "Freight, stock, and material movers, hand",
            "40": "Other helpers, equipment cleaners, hand packagers and laborers",
            "41": "Military occupations",
            "98": "Blank but applicable",
        }
        for code in ["OCD240", "OCD390", "OCD470"]:
            if code in self.var_dict:
                dct = self.var_dict[code]
                self.var_dict[code] = {
                    "Code": code,
                    "Name": dct["Name"],
                    "Category": dct["Category"],
                    "Codebook": code_dict,
                }
        return


    def _codebook_cleanup(self):
        """
        Add "Missing" values and "0" values for binary codes

        """
        for var in self.var_dict:
            var_data = self.var_dict[var]
            var_name = var_data["Name"].lower()
            codebook = var_data["Codebook"]
            # Recode (add 0 for binary 1/2 encodings)
            recode = False
            if var_name.find("gender") >= 0 and "0" not in codebook:
                recode = True
            if "1" in codebook and "2" in codebook and "3" not in codebook and "0" not in codebook:
                if codebook["1"].lower() == "yes" and codebook["2"].lower() == "no":
                    recode = True
            if recode:
                codebook["0"] = codebook["2"]
            # Sort codebook labels
            codebook = dict(sorted(codebook.items(), key=lambda x: x[0].lower()))
            codebook.pop(".", None)
            codebook.update({".": "Missing"})
            var_data["Codebook"] = codebook
            var_data["Recode"] = recode
            self.var_dict[var] = var_data
        return



    def _to_pandas(self):
        """
        Get codebook as pandas Dataframe
        
        Returns
        -------
        Dataframe
            Codebook dataframe

        """
        df = pd.DataFrame(self.var_dict).T
        df["Categories"] = np.array([_sort_categories(c) for c in df["Category"].values], dtype=str)
        df["Category"] = np.array([c.split(",")[0] for c in df["Categories"].values], dtype=str)
        df["Category Name"] = df["Category"].map(self.categ_dict).values
        df["Codebook"] = [json.dumps(c) for c in df["Codebook"].values]
        # Sort variables
        dfs = []
        group_dct = {}
        n = df.shape[0]
        sorted_categ = []
        categ_names = df["Category Name"].values
        categ_codes = df["Category"].values
        for component in self.survey_components:
            component_categs = sorted(self.component_dict[component])
            for categ in component_categs:
                if categ not in sorted_categ:
                    codes = []
                    for c in categ_codes[categ_names == categ]:
                        if c not in codes:
                            codes.append(c)
                    for code in codes:
                        mask = np.zeros((n)).astype(bool)
                        for i, c in enumerate(categ_codes):
                            if c == code:
                                mask[i] = True
                        dfs.append(df[mask])
                        group_dct[code] = component
                    sorted_categ.append(categ)
        # Add unsorted categories
        mask = np.zeros((n)).astype(bool)
        for i, c in enumerate(df["Category Name"].values):
            if c not in sorted_categ:
                mask[i] = True
        if np.sum(mask) > 0:
            dfs.append(df[mask])
        # Convert to single dataframe
        df = pd.concat(dfs, join="outer", axis=0)
        df.set_index("Code", inplace=True)
        # Add component column
        group = [group_dct[c] if c in group_dct else "Other" for c in df["Category"].values]
        df["Component"] = np.array(group, dtype=str)
        # Add variable combined names
        dct = _get_var_combined_names()
        combined = [dct[c] if c in dct else "" for c in df.index.values]
        df["Combined Name"] = np.array(combined, dtype=str)
        # Add category combined names
        dct = _get_categ_combined_names(df["Category"].values, df["Category Name"].values)
        combined = np.array([dct[c] for c in df["Category"].values], dtype=str)
        combined = _upd_categ_combined_names(df["Combined Name"].values, combined)
        df["Combined Category"] = combined
        columns = ["Name", "Combined Name", "Categories", "Category", "Category Name", 
                   "Combined Category", "Component", "Codebook", "Recode"]
        df = df[columns]
        return df

    
    def _to_csv(self, output_fname):
        """
        Save codebook to .csv
        
        Parameters
        ----------
        output_fname : str
            Path to output .csv

        Returns
        -------
        Dataframe
            Codebook dataframe

        """
        df = self._to_pandas()
        path = os.path.expanduser(output_fname)
        df.to_csv(path, sep=";")
        return
    

def _sort_categories(categories):
    """
    Sort unique comma-separated categories

    Parameters
    ----------
    categories : str
        Comma-separated all category codes

    Returns
    -------
    str
        Comma-separated unique category codes

    """
    categs = np.array(categories.split(","))
    value, count = np.unique(categs, return_counts=True)
    idx = np.argsort(count)[::-1]
    categs = "DEMO" if "DEMO" in value else ",".join(value[idx].tolist())
    return categs


def _get_categ_combined_names(catcodes, catnames):
    """
    Make dictionary of category combined names

    Parameters
    ----------
    catcodes : ndarray
        Category codes
    catnames : ndarray
        Category names

    Returns
    -------
    dict
        Dictionary Category codes -> Category combined names

    """
    dct = {}
    for i, c in enumerate(catcodes):
        if c not in dct:
            name = catnames[i]
            combined = name.split()[0]
            for key in ["Physical", "Muscle"]:
                if name.find(key) == 0:
                    combined = "Physical Activity"
            for key in ["Biochemistry", "Vitamin", "Urine"]:
                if name.find(key) >= 0:
                    combined = "Biochemistry"
            for key in ["Vision", "Ophthalmology"]:
                if name.find(key) >= 0:
                    combined = "Vision"
            for key in ["Blood Pressure"]:
                if name.find(key) >= 0:
                    combined = "Blood Pressure"
            for key in ["Virus", "HIV", "Hepatitis", "Hepatitis", "Herpes", "Papillomavirus", "Measles"]:
                if name.find(key) >= 0:
                    combined = "Virus"
            for key in ["X-ray", 'Ultrasound', "Impedance", "Childhood", "epididymal", "Glucose"]:
                if name.find(key) >= 0:
                    combined = key.title()
            name = re.sub('[:;,&\-]', ' ', name)
            if name.startswith("Diet"):
                combined = "Food"
            if c in ["AQQ", "RDQ"]:
                combined = "Respiratory"
            if c in ["HIQ", "HSQ"]:
                combined = "Health"
            if c in ["ARQ", "BPQ", "DIQ", "MCQ", "MPQ", "OSQ", "TBQ"]:
                combined = "Medical"
            if c in ["HUQ", "RXQ_RX"]:
                combined = "Hospitalization"
            if c in ["CBC", "COT", "LAB25", "L25_2"]:
                combined = "Blood Count"
            if c in ["FASTQX", "OGTT", "GLU", "PH", "INS"]:
                combined = "Glucose"
            if c in ["APOB", "CRP", "TRIGLY", "TCHOL", "L11", "L13", "L13_2", "L17", "LAB17", 
                     "BIOPRO", "TST", "LAB10", "LAB11", "LAB13", "LAB13AM", "LAB18", "INS", "EPP", 
                     "EPH", "SSPCB", "CUSEZN", "UAM", "EPHPP", "L24EPH", "THYROD", "FOLFMS", "SSAFB",
                     "IHGEM", "PBCD", "FETIB", "FOLATE", "SSFA", "GHB", "DOXPOL", "DEET", "HDL", 
                     "L28NPB", "SSBNP", "FAS", "SSCMV", "SSBFR", "SSTOXO", "POOLTF", "SSTOCA", "AMDGYD", 
                     "LAB06", "L06", "L06NB", "PFAS", "SSCARD", "L35", "SSMUMP", "SSPST", "L19_2", 
                     "PSTPOL", "PCBPOL", "BFRPOL", "L39_2", "L11_2", "L11P_2", "L10_2", "L40FE",
                     "L28PBE", "LAB06HM", "L06HM", "LAB06", "L06BMT", "L06", "PBCD", "L39", "L39EPP",
                     "LAB28POC", "ETHOX", "FORMAL", "LAB18T4", "L20", "LAB20"]:
                combined = "Biochemistry"
            dct[c] = combined
            dct["DUQ"] = "Drugs"
            dct["ACQ"] = "Demographic"
            dct["BMX"] = "Body Measures"
    return dct


def _upd_categ_combined_names(fnames, catnames):
    """
    Update category combined names based on variable combined names

    Parameters
    ----------
    fnames : ndarray
        Variable combined names
    catnames : ndarray
        Category combined names

    Returns
    -------
    ndarray
        Updated category combined names

    """
    for i, fname in enumerate(fnames):
        for key in ["Allergy", "Income", "Insurance", "Interview", "Occupation", "Respiratory"]:
            if fname.startswith(key):
                catnames[i] = key
        if fname.startswith("House"):
            catnames[i] = "Housing"
        if fname.startswith("Poverty"):
            catnames[i] = "Income"
        if fname.startswith("Health general"):
            catnames[i] = "Health"
        if fname.startswith("Consumer Behavior"):
            catnames[i] = "Food"
        if fname.startswith("Difficulty"):
            catnames[i] = "Disability"
        if fname.startswith("Depression"):
            catnames[i] = "Mental"
    return catnames


def _get_var_combined_names():
    """
    Make dictionary of variable combined names

    Returns
    -------
    dict
        Dictionary Variable codes -> Variable combined names

    """
    dct = {
        "SDDSRVYR": "Survey",
        "RIDSTATR": "Interview status",
        "RIDEXMON": "Season of year",
        "RIDAGEYR": "Age",
        "RIDAGEMN": "Age (Months)",
        "RIDAGEEX": "Age (Months)",
        "RIAGENDR": "Gender",
        "RIDRETH1": "Ethnicity",
        "RIDRETH2": "Ethnicity",
        "RIDRETH3": "Ethnicity",
        "DMQMILIT": "Served in army",
        "DMDBORN":  "Country of birth",
        "DMDCITZN": "Citizenship",
        "DMDYRSUS": "Time in US",
        "DMDEDUC":  "Education level",
        "DMDEDUC2": "Education level",
        "DMDEDUC3": "Education level",
        "DMDSCHOL": "Occupation school",
        "DMDMARTL": "Marital status",
        "DMDHHSIZ": "Householed people",
        "INDHHINC": "Income (Household, annual)",
        "INDHHIN2": "Income (Household, annual)",
        "INDFMINC": "Income (Family, annual)",
        "INDFMIN2": "Income (Family, annual)",
        "INDFMPIR": "Poverty status",
        "RIDEXPRG": "Pregnancy status",
        "RIDPREG":  "Pregnancy status",
        "DMDHRGND": "Gender",
        "DMDHRAGE": "Age",
        "DMDHRBRN": "Country of birth",
        "DMDHREDU": "Education level",
        "DMDHRMAR": "Marital status",
        "DMDHSEDU": "Education level (Spouse)",
        "WTINT2YR": "Sample weight",
        "SIALANG":  "Interview language",
        "SIAPROXY": "Interview proxy",
        "SIAINTRP": "Interview interpreter",
        "FIALANG":  "Interview language",
        "FIAPROXY": "Interview proxy",
        "FIAINTRP": "Interview interpreter",
        "MIALANG":  "Interview language",
        "MIAPROXY": "Interview proxy",
        "MIAINTRP": "Interview interpreter",
        "AIALANG":  "Interview language",
        "DMDFMSIZ": "Family people",
        "DMDBORN2": "Country of birth",
        "DMDHRBR2": "Country of birth",
        "RIDEXAGY": "Age",
        "RIDEXAGM": "Age (Months)",
        "DMQMILIZ": "Served in army",
        "DMQADFC":  "Served in army",
        "DMDBORN4": "Country of birth",
        "AIALANGA": "Interview language",
        "DMDHHSZA": "Householed chilred <= 5",
        "DMDHHSZB": "Householed chilred 6-17",
        "DMDHHSZE": "Householed elderly >=60",
        "DMDHRBR4": "Country of birth",
        "DMDHRAGZ": "Age",
        "DMDHREDZ": "Education level",
        "DMDHRMAZ": "Marital status",
        "DMDHSEDZ": "Education level (Spouse)",
        "ELIGSTAT": "Mortality eligibility status",
        "MORTSTAT": "Mortality event",
        "UCOD_LEADING": "Mortality cause",
        "DIABETES": "Mortality diabetes",
        "HYPERTEN": "Mortality hypertension",
        "DODQTR": "Mortality season",
        "DODYEAR": "Mortality year",
        "WGT_NEW": "Mortality person-level sample weight",
        "SA_WGT_NEW": "Mortality person-level sample weight",
        "PERMTH_INT": "Mortality tte",
        "PERMTH_EXM": "Mortality followup (Months)",
        "ACD010A": "Language",
        "ACD010B": "Language",
        "ACD010C": "Language",
        "ACQ020": "Language",
        "ACQ030": "Language",
        "ACD040": "Language",
        "ACQ050": "Language",
        "ACQ060": "Language",
        "ACD070": "Country of birth (Father)",
        "ACD080": "Country of birth (Mother)",
        "ACD011A": "Language",
        "ACD011B": "Language",
        "ACD011C": "Language",
        "ACD110": "Language",
        "PAQ685": "Air quality changed activity",
        "PAQ690A": "Air quality mask",
        "PAQ690B": "Air quality less outdoors",
        "PAQ690C": "Air quality avoid traffic",
        "PAQ690D": "Air quality less exercises",
        "PAQ690E": "Air quality medications",
        "PAQ690F": "Air quality closed windows",
        "PAQ690G": "Air quality less driving",
        "PAQ690H": "Air quality cancelled outdoor",
        "PAQ690I": "Air quality indoor exercises",
        "PAQ690J": "Air quality public transport",
        "PAQ690K": "Air quality use filter",
        "ALQ120Q": "Alcohol drinks days per year",
        "ALQ120U": "Alcohol drinks days per week",
        "ALQ130": "Alcohol drinks num per time",
        "AGQ010": "Allergy",
        "AGQ040": "Allergy",
        "ARQ020A": "Pain neck",
        "ARQ020B": "Pain back",
        "ARQ020C": "Pain back",
        "ARQ020D": "Pain back",
        "ARQ020F": "Pain hip",
        "ARQ020G": "Pain ribscage",
        "ARQ024A": "Pain neck",
        "ARQ024B": "Pain back",
        "ARQ024C": "Pain back",
        "ARQ024D": "Pain back",
        "ARQ024F": "Pain hip",
        "ARQ024G": "Pain ribscage",
        "MPQ060": "Pain neck",
        "MPQ070": "Pain back",
        "MPQ090": "Headache/Migraine",
        "BPQ030": "Hypertension",
        "CDQ010": "Respiratory Shortness of breath",
        "HSQ500": "Health cold",
        "HSQ510": "Health stomach",
        "HSQ520": "Health flu or pneumonia",
        "HSD010": "Health general",
        "HSQ470": "Health physical poor (days)",
        "HSQ480": "Health mental poor (days)",
        "HSQ490": "Health inactive (days)",
        "DIQ010": "Diabetes",
        "DBD090": "Food from restaurant weekly",
        "DBD195": "Food Milk consumed monthly",
        "DBD196": "Food Milk consumed monthly",
        "DBD197": "Food Milk consumed monthly",
        "DBQ197": "Food Milk consumed monthly",
        "DBD091": "Food from restaurant weekly",
        "DBD895": "Food from restaurant weekly",
        "DBQ920": "Allergy food",
        "DBQ925A": "Allergy wheat",
        "DBQ925B": "Allergy milk",
        "DBQ925C": "Allergy eggs",
        "DBQ925D": "Allergy fish",
        "DBQ925E": "Allergy shellfish",
        "DBQ925F": "Allergy corn",
        "DBQ925G": "Allergy peanut",
        "DBQ925H": "Allergy nuts",
        "DBQ925I": "Allergy soy",
        "DLQ010": "Difficulty hearing",
        "DLQ020": "Difficulty seeing",
        "DLQ040": "Difficulty concentrating",
        "DLQ050": "Difficulty walking",
        "DLQ060": "Difficulty dressing/bathing",
        "DLQ080": "Difficulty doing errands",
        "DLQ100": "Depression Worry/Anxious (frequency)",
        "DLQ110": "Depression Worry/Anxious (medication)",
        "DLQ130": "Depression Worry/Anxious (level)",
        "DLQ140": "Depression (frequency)",
        "DLQ150": "Depression (medication)",
        "DLQ170": "Depression (level)",
        "DUQ100": "Drugs cocaine",
        "DUQ120": "Drugs needle",
        "DUQ130": "Drugs needle (day/year)",
        "DUQ200": "Drugs marijuana or hashish",
        "DUQ230": "Drugs marijuana or hashish (day/month)",
        "DUQ280": "Drugs cocaine (day/month)",
        "DUQ290": "Drugs heroin",
        "DUQ320": "Drugs heroin (day/month)",
        "DUQ330": "Drugs methamphetamine",
        "DUQ360": "Drugs methamphetamine (day/month)",
        "DUQ370": "Drugs needle",
        "DUQ420": "Drugs needle (frequency)",
        "DUQ430": "Drugs rehabilitation",
        "DUQ211": "Drugs marijuana (month/year)",
        "DUQ213": "Drugs marijuana regularly",
        "DUQ215U": "Drugs marijuana quit",
        "DUQ217": "Drugs marijuana (frequency)",
        "HID010":  "Insurance health",
        "HID030A": "Insurance private",
        "HID030B": "Insurance Medicare",
        "HID030C": "Insurance Medicaid/CHIP",
        "HID030D": "Insurance government",
        "HID030E": "Insurance service plan",
        "HID040":  "Insurance dental",
        "HIQ011":  "Insurance health",
        "HIQ031A": "Insurance private",
        "HIQ031B": "Insurance Medicare",
        "HIQ031C": "Insurance Medi-Gap",
        "HIQ031D": "Insurance Medicaid/CHIP",
        "HIQ031E": "Insurance Medicaid/CHIP",
        "HIQ031F": "Insurance military",
        "HIQ031G": "Insurance indian",
        "HIQ031H": "Insurance state",
        "HIQ031I": "Insurance government",
        "HIQ031J": "Insurance service plan",
        "HIQ260":  "Insurance Medicare",
        "HUQ010":  "Health general",
        "HUQ020":  "Health now compared with 1 year ago",
        "HUQ050":  "Hospital/healthcare frequency last yr",
        "HUQ070":  "Hospitalization last yr",
        "HUD080":  "Hospitalization number last yr",
        "HUD070":  "Hospitalization last yr",
        "HUQ080":  "Hospitalization number last yr",
        "HUQ082":  "Hospitalization long term",
        "HUQ084":  "Hospitalization long term (day/ear)",
        "HUQ071":  "Hospitalization last yr",
        "HUQ051":  "Hospital/healthcare frequency last yr",
        "HOD010":  "House type",
        "HOD011":  "House type",
        "HOQ011":  "House type",
        "HOD030":  "House apartments",
        "HOD040":  "House built (year)",
        "HOQ040":  "House built (year)",
        "HOD050":  "House rooms",
        "HOD060":  "House years (years)",
        "HOQ065":  "House ownership",
        "HOQ240":  "House cockroaches",
        "HOQ250":  "House pets",
        "HOQ260A": "House dog",
        "INQ020": "Income from wages/salaries",
        "INQ012": "Income from self employment",
        "INQ030": "Income from social security",
        "INQ060": "Income from disability pension",
        "INQ080": "Income from retirement pension",
        "INQ090": "Income from supplemental security",
        "INQ132": "Income from state assistance",
        "INQ140": "Income from dividends or rental",
        "INQ150": "Income from other sources",
        "MCQ010": "Asthma",
        "MCQ030": "Asthma",
        "MCQ053": "Anemia",
        "MCQ060": "Attention deficit disorder",
        "MCQ080": "Overweight",
        "MCQ120A": "Allergy hay fever",
        "MCQ120B": "Ear infections",
        "MCQ120C": "Headache",
        "MCQ120D": "Stuttering or Stammering",
        "MCQ160A": "Arthritis",
        "MCQ160B": "Congestive heart failure (CHF)",
        "MCQ160C": "Coronary heart disease (CHD)",
        "MCQ160D": "Angina pectoris",
        "MCQ160E": "Heart attack (MI)",
        "MCQ160F": "Stroke",
        "MCQ160G": "Chronic lower resperatory diseases",
        "MCQ160H": "Goiter",
        "MCQ160I": "Thyroid condition",
        "MCQ160J": "Overweight",
        "MCQ160K": "Chronic lower resperatory diseases",
        "MCQ160L": "Liver condition",
        "MCQ170H": "Goiter",
        "MCQ170I": "Thyroid condition",
        "MCQ170K": "Chronic bronchitis",
        "MCQ170L": "Liver condition",
        "MCQ220": "Cancer of any kind",
        "MCQ160M": "Thyroid condition",
        "MCQ170M": "Thyroid condition",
        "AGQ030": "Allergy hay fever",
        "MCQ160N": "Gout",
        "MCQ070": "Psoriasis",
        "MCQ082": "Celiac disease",
        "MCQ160O": "Chronic lower resperatory diseases",
        "MCQ203": "Jaundice",
        "MCQ500": "Liver condition",
        "DPQ010": "Depression Low interest (frequency)", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ020": "Depression Feel hopeless (frequency)", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ030": "Depression Trouble sleeping (frequency)", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ040": "Depression Feel tired (frequency)", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ050": "Depression Trouble appetite (frequency)", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ060": "Depression Feel bad (frequency)", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ070": "Depression Trouble concentrating (frequency)", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ080": "Depression Trouble speaking (frequency)", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ090": "Depression Feel better dead (frequency)", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ100": "Depression Caused difficulties (frequency)", # {"0": "Not at all difficult", "1": "Somewhat difficult", "2": "Very difficult", "3": "Extremely difficult", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQD001": "Two weeks sad, empty, depressed?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD002": "Sad, empty, depressed (SED) frequency", # {"1": "Every Day", "2": "Nearly Every Day", "3": "Most Days", "4": "About Half the Days", "5": "Less than Half the Days", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD003": "SED duration", # {"1": "All Day Long", "2": "Most of the Day", "3": "About Half", "4": "Less than Half", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD004": "When SED, also other problems?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD005": "When SED, did you lack energy?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD006": "When SED, did you lose interest?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD007": "When SED, were you irritable or grouchy?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD008": "Past 12 mos. 2 weeks lost interest?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD009": "Loss of interest frequency", # {"1": "Every Day", "2": "Nearly Every Day", "3": "Most Days", "4": "About Half the Days", "5": "Less than Half the Days", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD010": "Loss of interest duration", # {"1": "All Day Long", "2": "Most of the Day", "3": "About half the days", "4": "Less than half the days", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD011": "When lost interest, also other problems?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD012": "When lost interest, did you lack energy?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD013": "When lost interest, irritable/grouchy?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD014": "Two weeks irritable most of the time?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD015": "Irritable frequency", # {"1": "Every Day", "2": "Nearly Every Day", "3": "Most Days", "4": "About Half the Days", "5": "Less than Half the Days", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD016": "Irritable duration", # {"1": "Every Day", "2": "Nearly Every Day", "3": "Most Days", "4": "About Half the Days", "5": "Less than Half the Days", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD017": "When irritable, also other problems?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD018": "When irritable, did you lack energy?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD019": "Did you have less appetite?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD020": "Did you lose weight?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD021": "Amount of weight lost", # {"1 to 26": "Range of Values", "1 to 30": "Range of Values", "100": "100 +", "2 to 20": "Range of Values", "777": "Refuse", "999": "Don't know", ".": "Missing"}
        "CIQD022": "Did you have a larger appetite?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD023": "Did you gain weight?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD024": "Amount of weight gained", # {"1 to 15": "Range of Values", "100": "100 +", "2 to 10": "Range of Values", "2 to 15": "Range of Values", "777": "Refuse", "999": "Don't know", ".": "Missing"}
        "CIQD025": "During 2 weeks, trouble sleep?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD026": "Frequency trouble sleeping", # {"1": "Every night", "2": "Nearly every night", "3": "Less often", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD027": "Did you wake up 2 hours early?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD028": "Did you sleep too much?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD029": "Felt bad first woke, better later?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD030": "Was interest in sex less than usual?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD031": "Lose ability to enjoy good things?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD032": "Did you talk or move more slowly?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD033": "Did anyone notice...more slowly?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD034": "Did you have to move all the time?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD035": "Did anyone notice moving all the time?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD036": "Did you feel worthless nearly every day?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD037": "Did you feel guilty?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD038": "Was there reason worthless or guilty?", # {"1": "Respondent Gives Reason", "5": "No Particular Reason", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD040": "Was R worthless/guilty about depression?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD041": "Did you feel not as good as others?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD042": "Did you have so little confidence...?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD043": "Did you have...trouble concentrating?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD044": "Unable to read...couldn't pay attention?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD045": "Did thoughts come slower...?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD046": "Were you unable to make up your mind...?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD047": "Did you think a lot about death?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD048": "Did you think about committing suicide?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD049": "Did you make a suicide plan?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD050": "Did you attempt suicide?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD051": "No. weeks SED... past 12 mos?", # {"0 to 52": "Range of Values", "2 to 52": "Range of Values", "77": "Refuse", "99": "Don't know", ".": "Missing"}
        "CIQD052": "Was it 1 period or 2 or more?", # {"1": "One Period", "2": "Two or more periods", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD053": "Was it one period or two or more?", # {"1": "One Period", "2": "Two or more periods", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD054": "Is the period still going on or ended?", # {"1": "Still going on", "5": "Ended", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD055": "How long has period been going on?", # {"16 to 432": "Range of Values", "2 to 72": "Range of Values", "72 to 180": "Range of Values", "777": "Refuse", "999": "Don't know", ".": "Missing"}
        "CIQD056": "When did it end -- past month or more?", # {"1": "Past Month", "2": "More than a month ago", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD057": "Ended past month, 6 months, more than 6?", # {"1": "Month", "2": "6 Months", "3": "More than 6 months ago", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD058": "How long period before it ended?", # {"1 to 416": "Range of Values", "1 to 48": "Range of Values", "1 to 52": "Range of Values", "7777": "Refuse", "9999": "Don't know", ".": "Missing"}
        "CIQD059": "Did period begin after someone died?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD060": "Who was it that died?", # {"1": "Spouse", "2": "Child", "3": "Parent/Sibling", "4": "Other Relative", "5": "Nonrelative", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD061": "Did period begin w_in month of baby?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD062": "Did anything else happen before period?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD064": "How many periods?", # {"2 to 10": "Range of Values", "2 to 12": "Range of Values", "2 to 26": "Range of Values", "77": "Refuse", "99": "Don't know", ".": "Missing"}
        "CIQD065": "Number of weeks before end?", # {"1 to 6": "Range of Values", "2 to 16": "Range of Values", "2 to 20": "Range of Values", "77": "Refuse", "99": "Don't know", ".": "Missing"}
        "CIQD066": "Did 1st per begin after someone died?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD067": "Who was it that died?", # {"1": "Spouse", "2": "Child", "3": "Parent/Sibling", "4": "Other Relative", "5": "Nonrelative", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD068": "Did period begin w_in month of baby?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD069": "Did anything else happen before period?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD071": "How long between two periods?", # {"14 to 210": "Range of Values", "4 to 300": "Range of Values", "7 to 180": "Range of Values", "7777": "Refuse", "9999": "Don't know", ".": "Missing"}
        "CIQD072": "Did you feel ok two months between?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD073": "Did you have two mos. enjoy...?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD074": "Is second period going on or ended?", # {"1": "Still going on", "5": "Ended", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD075": "How long went on before ended?", # {"1 to 42": "Range of Values", "2 to 150": "Range of Values", "7 to 30": "Range of Values", "7777": "Refuse", "9999": "Don't know", ".": "Missing"}
        "CIQD076": "Did it end past mos. or more than month?", # {"1": "Past Month", "2": "More than a month ago", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD077": "Did it end past mos, past 6 mos or more?", # {"1": "Month", "2": "Past 6 Months", "3": "More than 6 months", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD078": "Did 2nd period begin after someone died?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD079": "Who was it that died?", # {"1": "Spouse", "2": "Child", "3": "Parent/Sibling", "4": "Other Relative", "5": "Nonrelative", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD080": "Did 2nd period begin w_in a mon of baby?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD081": "Anything else happen before 2nd period?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD083": "What is longest  no weeks felt that way?", # {"1 to 12": "Range of Values", "1 to 24": "Range of Values", "1 to 8": "Range of Values", "77": "Refuse", "99": "Don't know", ".": "Missing"}
        "CIQD084": "Is most recent of 4 periods on or ended?", # {"1": "Still going on", "5": "Ended", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD085": "Ended past mos. or more than month?", # {"1": "Past Month", "2": "More than a month ago", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD086": "Ended past mos, six months or more?", # {"1": "Month", "2": "Past 6 Months", "3": "More than 6 months", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD087": "In between 4 periods, ok for 2 months?", # {"1": "Yes, I did not feel O.K.", "2": "No, felt O.K. between episodes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD088": "Between periods, 2 months activities?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD089": "Did 4 periods occur after someone died?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD090": "Who was it that died?", # {"1": "Spouse", "2": "Child", "3": "Parent/Sibling", "4": "Other Relative", "5": "Nonrelative", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD091": "Were all 4 periods after the death?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD092": "Did periods occur w_in mos. of baby?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD093": "Anything else happen before periods?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD095": "SED interfere with daily life?", # {"1": "A lot", "2": "Some", "3": "A little", "4": "Not at all", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQD096": "No. days totally unable work", # {"0 to 360": "Range of Values", "0 to 365": "Range of Values", "777": "Refused", "999": "Don't know", ".": "Missing"}
        "CIQD097": "Did day occur past 4 weeks?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD098": "No. days totally past 4 weeks", # {"0 to 28": "Range of Values", "0 to 99": "Range of Values", "777": "Refuse", "999": "Don't know", ".": "Missing"}
        "CIQD099": "No. days cutback amount/quality", # {"0 to 200": "Range of Values", "0 to 265": "Range of Values", "0 to 300": "Range of Values", "777": "Refuse", "999": "Don't know", ".": "Missing"}
        "CIQD100": "Describe quantity/quality cutback", # {"0 to 100": "Range of Values", "0 to 98": "Range of Values", "777": "Refuse", "999": "Don't know", ".": "Missing"}
        "CIQD101": "Did cutback occur past 4 weeks?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD102": "No. days cutback past 4 weeks", # {"0 to 14": "Range of Values", "0 to 20": "Range of Values", "0 to 28": "Range of Values", "77": "Refuse", "99": "Don't know", ".": "Missing"}
        "CIQD103": "No. days extreme effort to work", # {"0 to 280": "Range of Values", "0 to 320": "Range of Values", "0 to 365": "Range of Values", "777": "Refuse", "999": "Don't know", ".": "Missing"}
        "CIQD104": "Did extreme occur past 4 weeks?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD105": "No. days extreme past 4 weeks", # {"0 to 15": "Range of Values", "0 to 28": "Range of Values", "77": "Refuse", "99": "Don't know", ".": "Missing"}
        "CIQD106": "No. days interfere personal life", # {"0 to 365": "Range of Values", "777": "Refuse", "999": "Don't know", ".": "Missing"}
        "CIQD107": "Did interfere occur past 4 weeks?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD108": "No. days interfere past 4 weeks", # {"0 to 28": "Range of Values", "77": "Refuse", "99": "Don't know", ".": "Missing"}
        "CIQD109": "Did you tell MD about SED?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQDPA": "Did you tell other prof about SED?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQDPB": "Did you take medication for SED?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQDPC": "Did SED interfere with life?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQDPD": "Was SED result of physical illness?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQDPE": "Was SED result of MDA?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQDPF": "Was SED always result MDA?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQDPHA": "Doctor said nerves causing SED", # {"1": "Doctor said nerves causing SED", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQDPHB": "Doctor said stress causing SED", # {"2": "Doctor said stress causing SED", ".": "Missing"}
        "CIQDPHC": "Doctor said anxiety causing SED", # {"3": "Doctor said anxiety causing SED", ".": "Missing"}
        "CIQDPHD": "Doctor said depression causing SED", # {"4": "Doctor said depression causing SED", ".": "Missing"}
        "CIQDPHE": "Doctor said mental illness causing SED", # {"5": "Doctor said mental illness causing SED", ".": "Missing"}
        "CIQDPHF": "Doctor said medication causing SED", # {"6": "Doctor said medication causing SED", ".": "Missing"}
        "CIQDPHG": "Doctor said drugs causing SED", # {"7": "Doctor said drugs causing SED", ".": "Missing"}
        "CIQDPHH": "Doctor said alcohol causing SED", # {"8": "Doctor said alcohol causing SED", ".": "Missing"}
        "CIQDPHI": "Doctor said physical illness causing SED", # {"9": "Doctor said physical illness causing SED", ".": "Missing"}
        "CIQDPHJ": "Doctor said physical injury causing SED", # {"10": "Doctor said physical injury causing SED", ".": "Missing"}
        "CIQDPHK": "Doctor gave no definite diag for SED", # {"11": "Doctor gave no definite diagnosis for SED", ".": "Missing"}
        "CIQDPJ": "Was SED always result MDA?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQDPK": "Was SED result of phys illness?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQDPQ": "Anything abnormal when exam?", # {"1": "Nothing abnormal", "2": "No examination", "3": "Something abnormal", "5": "Something abnormal", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQDPN": "Was SED always result of phys illness?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQDPO": "When not ill,was SED always result MDA ?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIDDPRB": "General cause of problem", # {"1": "No Problem", "2": "Not clinically significant", "3": "Medication, Drugs, or Alcohol", "4": "Physical cause", "5": "Psychiatric Symptom", ".": "Missing"}
        "CIQD110": "Remember age when first SED?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQD111": "How old were you when first SED?", # {"2 to 39": "Range of Values", "6 to 38": "Range of Values", "7 to 39": "Range of Values", "77": "Refuse", "99": "Don't know", ".": "Missing"}
        "CIQD112": "About how old when first SED?", # {"5 to 30": "Range of Values", "5 to 33": "Range of Values", "7 to 37": "Range of Values", "77": "Refuse", "99": "Don't know", ".": "Missing"}
        "CIQD113": "Earliest age period of SED", # {"4 to 33": "Range of Values", "5 to 38": "Range of Values", "7 to 37": "Range of Values", "77": "Refuse", "99": "Don't know", ".": "Missing"}
        "CIDDSCOR": "Depression Score", # {"1": "Positive Diagnosis", "5": "Negative Diagnosis", ".": "Missing"}
        "DPQ010": "Have little interest in doing things", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ020": "Feeling down, depressed, or hopeless", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ030": "Trouble sleeping or sleeping too much", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ040": "Feeling tired or having little energy", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ050": "Poor appetite or overeating", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ060": "Feeling bad about yourself", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ070": "Trouble concentrating on things", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ080": "Moving or speaking slowly or too fast", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ090": "Thought you would be better off dead", # {"0": "Not at all", "1": "Several days", "2": "More than half the days", "3": "Nearly every day", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "DPQ100": "Difficulty these problems have caused", # {"0": "Not at all difficult", "1": "Somewhat difficult", "2": "Very difficult", "3": "Extremely difficult", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG01": "Month worried, tense, anxious(WTA)?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG02": "Did period last six months?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG03": "No. months worried, tense, anxious?", # {"1 to 12": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQG04": "WTA frequency", # {"1": "Every Day", "2": "Nearly Every Day", "3": "Most Days", "4": "About Half the Days", "5": "Less than Half the Days", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG05": "WTA duration", # {"1": "All Day Long", "2": "Most of the Day", "3": "About Half", "4": "Less than Half", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG06": "WTA a lot more than most?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG07": "Did period last six months?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG08": "No. months worried, tense, anxious?", # {"0 to 12": "Range of Values", "0 to 6": "Range of Values", "0 to 8": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQG09": "WTA frequency", # {"1": "Every Day", "2": "Nearly Every Day", "3": "Most Days", "4": "About Half the Days", "5": "Less than Half the Days", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG10": "WTA duration", # {"1": "All Day Long", "2": "Most of the Day", "3": "About Half", "4": "Less than Half", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG12": "Did R worry about health/drug use?", # {"0": "No", "1": "Yes", ".": "Missing"}
        "CIQG13": "Did R have multiple worries?", # {"1": "Worried about one thing", "2": "Multiple worries", ".": "Missing"}
        "CIQG14": "Do you think worry excessive?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG15": "How often difficult control worry?", # {"1": "Often", "2": "Sometimes", "3": "Rarely", "4": "Never", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG16": "How often worry so strong?", # {"1": "Often", "2": "Sometimes", "3": "Rarely", "4": "Never", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG17A": "Handcard: often restless?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG17B": "Handcard: often keyed up?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG17C": "Handcard: more tired than usual?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG17D": "Handcard: more irritable?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG17E": "Handcard: trouble sleeping?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG17F": "Handcard: trouble keeping mind on?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG17G": "Handcard: tense, sore, ach muscles?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG18": "Did you tell MD about WTA?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQGPA": "Did you tell other prof about WTA?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQGPB": "Did you take medication for WTA?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQGPC": "Did WTA interfere with life?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQGPD": "Was WTA result of phys illness?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQGPE": "Was WTA result of  MDA?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQGPF": "Was WTA always result MDA?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQGPHA": "Doctor said nerves causing WTA", # {"1": "Doctor said nerves causing WTA", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQGPHB": "Doctor said stress causing WTA", # {"2": "Doctor said stress causing WTA", ".": "Missing"}
        "CIQGPHC": "Doctor said anxiety causing WTA", # {"3": "Doctor said anxiety causing WTA", ".": "Missing"}
        "CIQGPHD": "Doctor said depression causing WTA", # {"4": "Doctor said depression causing WTA", ".": "Missing"}
        "CIQGPHE": "Doctor said mental illness causing WTA", # {"5": "Doctor said mental illness causing WTA", ".": "Missing"}
        "CIQGPHF": "Doctor said medication causing WTA", # {"6": "Doctor said medication causing WTA", ".": "Missing"}
        "CIQGPHG": "Doctor said drugs causing WTA", # {"7": "Doctor said drugs causing WTA", ".": "Missing"}
        "CIQGPHH": "Doctor said alcohol causing WTA", # {"8": "Doctor said alcohol causing WTA", ".": "Missing"}
        "CIQGPHI": "Doctor said physical illness causing WTA", # {"9": "Doctor said physical illness causing WTA", ".": "Missing"}
        "CIQGPHJ": "Doctor said physical injury causing WTA", # {"10": "Doctor said physical injury causing WTA", ".": "Missing"}
        "CIQGPHK": "Doc gave no definite diagnosis for WTA", # {"11": "Doc gave no definite diagnosis for WTA", ".": "Missing"}
        "CIQGPJ": "Was WTA always result MDA?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQGPK": "Was WTA result of phys illness?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQGPQ": "Anything abnormal when examined?", # {"1": "Nothing abnormal", "2": "No examination", "3": "Something abnormal", "5": "Something abnormal", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQGPN": "Was WTA always result of phys illness?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQGPO": "Was WTA always result MDA?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIDGPRB": "General cause of WTA", # {"1": "No Problem", "2": "Not clinically significant", "3": "Medication, Drugs, or Alcohol", "4": "Physical cause", "5": "Psychiatric Symptom", ".": "Missing"}
        "CIQG19": "Remember age when first WTA?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG20": "How old were you when first WTA?", # {"5 to 38": "Range of Values", "7 to 39": "Range of Values", "77": "Refused", "9 to 38": "Range of Values", "99": "Don't know", ".": "Missing"}
        "CIQG21": "About how old when first WTA?", # {"14 to 31": "Range of Values", "7 to 36": "Range of Values", "77": "Refused", "9 to 39": "Range of Values", "99": "Don't know", ".": "Missing"}
        "CIQG22": "Earliest age period of WTA", # {"14 to 39": "Range of Values", "4 to 39": "Range of Values", "77": "Refused", "9 to 39": "Range of Values", "99": "Don't know", ".": "Missing"}
        "CIQG23": "Recency of period of WTA", # {"1": "Past Month", "2": "Past 6 Months", "3": "Over 6 Months", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG24": "How upset for feeling WTA?", # {"1": "Very upset", "2": "Somewhat Upset", "3": "Not very upset", "4": "Not at all upset", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG25": "WTA interfere with daily life?", # {"1": "A lot", "2": "Some", "3": "A little", "4": "Not at all", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG26": "No. days totally unable work", # {"0 to 260": "Range of Values", "0 to 270": "Range of Values", "0 to 365": "Range of Values", "777": "Refused", "999": "Don't know", ".": "Missing"}
        "CIQG27": "Did day occur past 4 weeks?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG28": "No. days totally past 4 weeks", # {"0 to 20": "Range of Values", "0 to 28": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQG29": "No. days cutback amount/quality", # {"0 to 260": "Range of Values", "0 to 365": "Range of Values", "777": "Refused", "999": "Don't know", ".": "Missing"}
        "CIQG30": "Describe quantity/quality cutback", # {"0 to 100": "Range of Values", "0 to 85": "Range of Values", "777": "Refused", "999": "Don't know", ".": "Missing"}
        "CIQG31": "Did cutback occur past 4 weeks?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG32": "No. days cutback past 4 weeks", # {"0 to 12": "Range of Values", "0 to 28": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQG33": "No. days extreme effort to work", # {"0 to 180": "Range of Values", "0 to 200": "Range of Values", "777": "Refused", "999": "Don't know", ".": "Missing"}
        "CIQG34": "Did extreme occur past 4 weeks?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG35": "No. days extreme past 4 weeks", # {"0 to 10": "Range of Values", "0 to 14": "Range of Values", "0 to 20": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQG36": "No. days interfere personal life", # {"0 to 365": "Range of Values", "777": "Refused", "999": "Don't know", ".": "Missing"}
        "CIQG37": "Did interfere occur past 4 weeks?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQG38": "No. days interfere past 4 weeks", # {"0 to 28": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIDGSCOR": "GAD score", # {"1": "Positive Diagnosis", "5": "Negative Diagnosis", ".": "Missing"}
        "WTSCI2YR": "CIDI Subsample 2 year MEC Weight", # {"2594.9999622 to 453821.755": "Range of Values", "3141.6834177 to 398125.62182": "Range of Values", "5216.6934328 to 300649.07751": "Range of Values", ".": "Missing"}
        "WTSCI4YR": "CIDI Subsample 4 Year MEC Weight", # {"1616.579907 to 231377.0998": "Range of Values", "1820.5187288 to 230304.53472": "Range of Values", ".": "Missing"}
        "CIAORDER": "Order in which CIDI modules are asked", # {"0": "Panic, GAD, Depression", "1": "Depression, Panic, GAD", ".": "Missing"}
        "CIQP01": "Entire life, ever fear or panic attack?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQP02": "Ever attack for no reason, out of blue?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQP03": "Panic attack past 12 months?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQP04": "Past 12 mos, avoided situations bc fear?", # {"0": "No", "1": "Yes", "7": "Refuse", "9": "Don't know", ".": "Missing"}
        "CIQP05": "How recently avoided situations bc fear?", # {"1": "Past Month", "2": "Past 6 Months", "3": "Over 6 Months", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP06": "Past 12 mos, month + fear another?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP07": "How recently have concern?", # {"1": "Past Month", "2": "Past 6 Months", "3": "Over 6 Months", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP08": "Past 12 mos, attacks lead to terrible?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP09": "How recently have concern?", # {"1": "Past Month", "2": "Past 6 Months", "3": "Over 6 Months", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP11": "Ever attacks life-threatening?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP12": "Ever attacks not life-threatening?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13A": "Did your heart pound or race?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13B": "Did you sweat?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13C": "Did you tremble or shake?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13D": "Did you have dry mouth?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13E": "Were you short of breath?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13F": "Feel like you were choking?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13G": "Pain or discomfort in chest?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13H": "Nausea or discomfort in stomach?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13I": "Were you dizzy or feeling faint?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13J": "Did you feel unreal?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13K": "Feel things around you unreal?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13L": "Afraid you might lose control?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13M": "Afraid you might pass out?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13N": "Afraid you might die?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13O": "Have hot flashes or chills?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP13P": "Have numbness or tingling?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP14": "No. attacks w/symptoms in lifetime?", # {"1 to 400": "Range of Values", "1 to 800": "Range of Values", "7777": "Refused", "900": "900 +", "9999": "Don't know", ".": "Missing"}
        "CIQP15": "Problems began suddenly, got worse?", # {"1": "Yes", "2": "Sometimes", "3": "No", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP16": "When did attack occur?", # {"1": "Past Month", "2": "Past 6 Months", "3": "Over 6 Months", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP17": "Remember age when attack occurred?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP18": "Exact age when attack occurred", # {"33": "33", "7777": "Refused", "9999": "Don't know", ".": "Missing"}
        "CIQP19": "Approx age when attack occurred", # {"7777": "Refused", "9999": "Don't know", ".": "Missing"}
        "CIQP20": "Which of three situations occurred?", # {"1": "Out of the blue", "2": "A situation where R had an unreasonably", "3": "A situation of real danger", "7777": "Refused", "9999": "Don't know", ".": "Missing"}
        "CIQP21A": "Fear: Giving a speech", # {"1": "Fear: Giving a speech", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQP21B": "Fear: Party or social event", # {"2": "Fear: Party or social event", ".": "Missing"}
        "CIQP21C": "Fear: Being in a crowd", # {"3": "Fear: Being in a crowd", ".": "Missing"}
        "CIQP21D": "Fear: Meeting new people", # {"4": "Fear: Meeting new people", ".": "Missing"}
        "CIQP21E": "Fear: Being outside, away", # {"5": "Fear: Being outside, away", ".": "Missing"}
        "CIQP21F": "Fear: Traveling bus, train, car", # {"6": "Fear: Traveling bus, train, car", ".": "Missing"}
        "CIQP21G": "Fear:  Crowd, standing in line", # {"7": "Fear: Crowd, standing in line", ".": "Missing"}
        "CIQP21H": "Fear: Being in a public place", # {"8": "Fear: Being in a public place", ".": "Missing"}
        "CIQP21I": "Fear: Animals", # {"9": "Fear: Animals", ".": "Missing"}
        "CIQP21J": "Fear: Heights", # {"10": "Fear: Heights", ".": "Missing"}
        "CIQP21K": "Fear: Storms, thunder, lightening", # {"11": "Fear: Storms, thunder, lightening", ".": "Missing"}
        "CIQP21L": "Fear: Flying", # {"12": "Fear: Flying", ".": "Missing"}
        "CIQP21M": "Fear: Closed spaces", # {"13": "Fear: Closed spaces", ".": "Missing"}
        "CIQP21N": "Fear: Seeing blood", # {"14": "Fear: Seeing blood", ".": "Missing"}
        "CIQP21O": "Fear: Getting an injection", # {"15": "Fear: Getting an injection", ".": "Missing"}
        "CIQP21P": "Fear: Going to the dentist", # {"16": "Fear: Going to the dentist", ".": "Missing"}
        "CIQP21Q": "Fear: Going to a hospital", # {"17": "Fear: Going to a hospital", ".": "Missing"}
        "CIQP21R": "Fear: Other 1", # {"18": "Fear: Other 1", ".": "Missing"}
        "CIQP21S": "Fear: Other 2", # {"19": "Fear: Other 2", ".": "Missing"}
        "CIQP21T": "Fear: Other 3", # {"20": "Fear: Other 3", ".": "Missing"}
        "CIQP23": "Exact age when attack occurred", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP24": "Years of age attack occurred", # {"4 to 34": "Range of Values", "5 to 39": "Range of Values", "77": "Refused", "9 to 39": "Range of Values", "99": "Don't know", ".": "Missing"}
        "CIQP25": "When did attack occur?", # {"1": "Past Month", "2": "Past 6 Months", "3": "More than 6 months", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP26": "Attack in past 12 months,or more?", # {"1": "Past 12 months", "2": "More than 12 months", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP27": "When did attack occur?", # {"1": "Past month", "2": "Past 6 months", "3": "More than 6 months", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP28": "Approx age when attack occurred", # {"13 to 27": "Range of Values", "5 to 39": "Range of Values", "7 to 30": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQP29": "Earliest age attack occurred", # {"12 to 30": "Range of Values", "13 to 27": "Range of Values", "5 to 39": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQP31": "Age last time had attack", # {"18 to 36": "Range of Values", "23 to 39": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQP32": "No. attacks lifetime out of the blue", # {"0 to 100": "Range of Values", "0 to 365": "Range of Values", "0 to 400": "Range of Values", "7777": "Refused", "900": "900 +", "9999": "Don't know", ".": "Missing"}
        "CIQP33": "Out of the blue attack past 12 months?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP34": "No. attacks past 12 months?", # {"0 to 100": "Range of Values", "0 to 30": "Range of Values", "1 to 100": "Range of Values", "7777": "Refused", "900": "900 +", "9999": "Don't know", ".": "Missing"}
        "CIQP36": "No. attacks lifetime strong fear", # {"0 to 105": "Range of Values", "0 to 799": "Range of Values", "0 to 80": "Range of Values", "7777": "Refused", "900": "900 +", "9999": "Don't know", ".": "Missing"}
        "CIQP37": "Attack in past 12 months?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP38": "No. attacks past 12 months?", # {"0 to 10": "Range of Values", "0 to 200": "Range of Values", "0 to 50": "Range of Values", "7777": "Refused", "900": "900 +", "9999": "Don't know", ".": "Missing"}
        "CIQP40": "No. attacks lifetime real danger", # {"0 to 15": "Range of Values", "0 to 40": "Range of Values", "0 to 5": "Range of Values", "7777": "Refused", "900": "900 +", "9999": "Don't know", ".": "Missing"}
        "CIQP41": "Attack in past 12 months?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP42": "No. attacks past 12 months", # {"0 to 2": "Range of Values", "0 to 3": "Range of Values", "7777": "Refused", "900": "900 +", "9999": "Don't know", ".": "Missing"}
        "CIQP44A": "Strong Fear: Giving a speech", # {"1": "Strong Fear: giving speech", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQP44B": "Strong Fear: Party or social event", # {"2": "Strong Fear: Party or social event", ".": "Missing"}
        "CIQP44C": "Strong Fear: Being in a crowd", # {"3": "Strong Fear: Being in a crowd", ".": "Missing"}
        "CIQP44D": "Strong Fear: Meeting new people", # {"4": "Strong Fear: Meeting new people", ".": "Missing"}
        "CIQP44E": "Strong Fear: Being outside, away", # {"5": "Strong Fear: Being outside, away", ".": "Missing"}
        "CIQP44F": "Strong Fear: Traveling bus, train, car", # {"6": "Strong Fear: Traveling bus, train, car", ".": "Missing"}
        "CIQP44G": "Strong Fear:  Crowd, standing in line", # {"7": "Strong Fear: Crowd, standing in line", ".": "Missing"}
        "CIQP44H": "Strong Fear: Being in a public place", # {"8": "Strong Fear: Being in a public place", ".": "Missing"}
        "CIQP44I": "Strong Fear: Animals", # {"9": "Strong Fear: Animals", ".": "Missing"}
        "CIQP44J": "Strong Fear: Heights", # {"10": "Strong Fear: Heights", ".": "Missing"}
        "CIQP44K": "Strong Fear: Storms, thunder, lightning", # {"11": "Strong Fear: Storms, thunder, lightning", ".": "Missing"}
        "CIQP44L": "Strong Fear: Flying", # {"12": "Strong Fear: Flying", ".": "Missing"}
        "CIQP44M": "Strong Fear: Closed spaces", # {"13": "Strong Fear: Closed spaces", ".": "Missing"}
        "CIQP44N": "Strong Fear: Seeing blood", # {"14": "Strong Fear: Seeing blood", ".": "Missing"}
        "CIQP44O": "Strong Fear: Getting an injection", # {"15": "Strong Fear: Getting an injection", ".": "Missing"}
        "CIQP44P": "Strong Fear: Going to the dentist", # {"16": "Strong Fear: Going to the dentist", ".": "Missing"}
        "CIQP44Q": "Strong Fear: Going to a hospital", # {"17": "Strong Fear: Going to a hospital", ".": "Missing"}
        "CIQP44R": "Strong Fear: Other 1", # {"18": "Strong Fear: Other 1", ".": "Missing"}
        "CIQP44S": "Strong Fear: Other 2", # {"19": "Strong Fear: Other 2", ".": "Missing"}
        "CIQP44T": "Strong Fear: Other 3", # {"20": "Strong Fear: Other 3", ".": "Missing"}
        "CIQP49": "Recency of fear or out of the blue", # {"1": "Past Month", "2": "Past 6 Months", "3": "Over 6 Months", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP50": "Past 12 months no. weeks attack", # {"0 to 40": "Range of Values", "0 to 52": "Range of Values", "1 to 52": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQP51": "Single period or two+ periods?", # {"1": "All in a row", "2": "2 or more periods", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP52": "Longest no. weeks in row attack", # {"0 to 22": "Range of Values", "0 to 24": "Range of Values", "0 to 36": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQP54": "Largest no. attacks any one week", # {"1 to 14": "Range of Values", "1 to 40": "Range of Values", "1 to 50": "Range of Values", "7777": "Refused", "900": "900 +", "9999": "Don't know", ".": "Missing"}
        "CIQP55": "Largest no. attack any four weeks", # {"1 to 4": "Range of Values", "2 to 4": "Range of Values", "4 to 6": "Range of Values", "7777": "Refused", "900": "900 +", "9999": "Don't know", ".": "Missing"}
        "CIQP57": "Four week in row and four attacks?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP58": "Did you tell doctor about attack?", # {"0": "No", "1": "Yes", ".": "Missing"}
        "CIQPPA": "Did you tell other prof about attack?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQPPB": "Did you take medication for attack?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQPPC": "Did attacks interfere with life?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQPPD": "Were attacks result of phys illness?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQPPE": "Were attacks result of  MDA?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQPPF": "Were attacks always result MDA?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQPPHA": "Doctor said nerves causing attacks", # {"1": "Doctor said nerves causing attacks", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQPPHB": "Doctor said stress causing attacks", # {"2": "Doctor said stress causing attacks", ".": "Missing"}
        "CIQPPHC": "Doctor said anxiety causing attacks", # {"3": "Doctor said anxiety causing attacks", ".": "Missing"}
        "CIQPPHD": "Doctor said depression causing attacks", # {"4": "Doctor said depression causing attacks", ".": "Missing"}
        "CIQPPHE": "Doc said mental illness causing attacks", # {"5": "Doc said mental illness causing attacks", ".": "Missing"}
        "CIQPPHF": "Doctor said medication causing attacks", # {"6": "Doctor said medication causing attacks", ".": "Missing"}
        "CIQPPHG": "Doctor said drugs causing attacks", # {"7": "Doctor said drugs causing attacks", ".": "Missing"}
        "CIQPPHH": "Doctor said alcohol causing attacks", # {"8": "Doctor said alcohol causing attacks", ".": "Missing"}
        "CIQPPHI": "Doctor said phys illness causing attacks", # {"9": "Doctor said phys illness causing attacks", ".": "Missing"}
        "CIQPPHJ": "Doctor said phys injury causing attacks", # {"10": "Doctor said phys injury causing attacks", ".": "Missing"}
        "CIQPPHK": "Doctor gave no definite diag for attacks", # {"11": "Doctor gave no definite diagnosis for attacks", ".": "Missing"}
        "CIQPPJ": "Were attacks always result MDA?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQPPK": "Were attacks result of phys illness?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQPPQ": "Anything abnormal when exam?", # {"1": "Nothing abnormal", "2": "No examination", "3": "Something abnormal", "5": "Something abnormal", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQPPN": "Were attacks always result phys ill?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQPPO": "Were attacks always result MDA?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIDPPRB": "General cause of problem", # {"1": "No Problem", "2": "Not clinically significant", "3": "Medication, Drugs, or Alcohol", "4": "Physical cause", "5": "Psychiatric Symptom", ".": "Missing"}
        "CIQP59": "Attacks interfere with daily life?", # {"1": "A lot", "2": "Some", "3": "A little", "4": "Not at all", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP60": "No. days totally unable work", # {"0 to 200": "Range of Values", "0 to 220": "Range of Values", "0 to 365": "Range of Values", "777": "Refused", "999": "Don't know", ".": "Missing"}
        "CIQP61": "Did totally occur past 4 weeks?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP62": "No. days totally past 4 weeks", # {"0 to 14": "Range of Values", "0 to 28": "Range of Values", "0 to 8": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQP63": "No. days cutback amount/quality", # {"0 to 190": "Range of Values", "0 to 265": "Range of Values", "0 to 300": "Range of Values", "777": "Refused", "999": "Don't know", ".": "Missing"}
        "CIQP64": "Describe quantity/quality cutback", # {"0 to 100": "Range of Values", "0 to 75": "Range of Values", "0 to 85": "Range of Values", "777": "Refused", "999": "Don't know", ".": "Missing"}
        "CIQP65": "Did cutback occur past 4 weeks?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP66": "No. days cutback past 4 weeks", # {"0 to 10": "Range of Values", "0 to 14": "Range of Values", "0 to 28": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQP67": "No. days extreme effort to work", # {"0 to 180": "Range of Values", "0 to 182": "Range of Values", "0 to 350": "Range of Values", "777": "Refused", "999": "Don't know", ".": "Missing"}
        "CIQP68": "Did extreme occur past 4 weeks?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP69": "No. days extreme past 4 weeks", # {"0 to 10": "Range of Values", "0 to 4": "Range of Values", "0 to 5": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIQP70": "No. days interfere personal life", # {"0 to 300": "Range of Values", "0 to 365": "Range of Values", "777": "Refused", "999": "Don't know", ".": "Missing"}
        "CIQP71": "Did interfere occur past 4 weeks?", # {"0": "No", "1": "Yes", "7": "Refused", "9": "Don't know", ".": "Missing"}
        "CIQP72": "No. days interfere past 4 weeks", # {"0 to 28": "Range of Values", "0 to 7": "Range of Values", "77": "Refused", "99": "Don't know", ".": "Missing"}
        "CIDPSCOR": "Panic Score", # {"1": "Positive Diagnosis", "5": "Negative Diagnosis", ".": "Missing"}        "OCQ130": "Occupation hours worked (category)",
        "OCQ150": "Occupation status",
        "OCQ180": "Occupation hours worked",
        "OCQ210": "Occupation hours worked (category)",
        "OCD240": "Occupation group 1999-2004",
        "OCQ260": "Occupation status",
        "OCQ280": "Insurance at job",
        "OCQ380": "Occupation Unemployment status",
        "OCD390": "Occupation group 1999-2004",
        "OCD150": "Occupation status",
        "OCD180": "Occupation hours worked",
        "OCD241": "Occupation group 2005-2012",
        "OCQ265": "Occupation shift-work",
        "OCD392": "Occupation group 2005-2012",
        "OSQ010A": "Broken hip",
        "OSQ060":  "Osteoporosis",
        "PAD020":  "Physical activity walk or bicycle",
        "PAQ180":  "Physical activity average",
        "PAD200":  "Physical activity vigorous",
        "PAD320":  "Physical activity moderate",
        "PAQ480":  "Physical activity hours at computer or TV",
        "PAD480":  "Physical activity hours at computer or TV",
        "PAD590":  "Physical activity hours at computer or TV",
        "PAD600":  "Physical activity hours at computer or TV",
        "PAQ605":  "Physical activity vigorous (Work)",
        "PAQ620":  "Physical activity moderate (Work)",
        "PAQ635":  "Physical activity walk or bicycle",
        "PAQ650":  "Physical activity vigorous",
        "PAQ665":  "Physical activity moderate",
        "PADLEVEL": "Physical activity level",
        "PADDURAT": "Physical activity duration [min]",
        "PAQ706":  "Physical activity days/week",
        "PAQ710":  "Physical activity hours at computer or TV",
        "PAQ715":  "Physical activity hours at computer or TV",
        "PFQ056":  "Difficulty confusion or memory problems",
        "PFQ059":  "Difficulty physical, mental, emotional",
        "PFQ060A": "Difficulty managing money",
        "PFQ060B": "Difficulty walking for a quarter mile",
        "PFQ060C": "Difficulty walking up ten stairs",
        "PFQ060D": "Difficulty stooping, crouching, kneeling",
        "PFQ060E": "Difficulty lifting or carrying",
        "PFQ060F": "Difficulty doing house chores",
        "PFQ060G": "Difficulty preparing meals",
        "PFQ060H": "Difficulty walking between rooms",
        "PFQ060I": "Difficulty standing up from armless chair",
        "PFQ060J": "Difficulty getting out of bed",
        "PFQ060K": "Difficulty using fork and knife",
        "PFQ060L": "Difficulty dressing yourself",
        "PFQ060M": "Difficulty standing for long periods",
        "PFQ060N": "Difficulty sitting for long periods",
        "PFQ060O": "Difficulty reaching up",
        "PFQ060P": "Difficulty grasping small objects",
        "PFQ060Q": "Difficulty going out to movies or events",
        "PFQ060R": "Difficulty attending social events",
        "PFQ060S": "Difficulty with home leisure activities",
        "PFD067A": "Difficulty due to health problems",
        "PFD067B": "Difficulty due to health problems",
        "PFD067C": "Difficulty due to health problems",
        "PFD067D": "Difficulty due to health problems",
        "PFD067E": "Difficulty due to health problems",
        "PFQ057":  "Difficulty confusion or memory problems",
        "PFQ061A": "Difficulty managing money",
        "PFQ061B": "Difficulty walking for a quarter mile",
        "PFQ061C": "Difficulty walking up ten stairs",
        "PFQ061D": "Difficulty stooping, crouching, kneeling",
        "PFQ061E": "Difficulty lifting or carrying",
        "PFQ061F": "Difficulty doing house chores",
        "PFQ061G": "Difficulty preparing meals",
        "PFQ061H": "Difficulty walking between rooms",
        "PFQ061I": "Difficulty standing up from armless chair",
        "PFQ061J": "Difficulty getting out of bed",
        "PFQ061K": "Difficulty using fork and knife",
        "PFQ061L": "Difficulty dressing yourself",
        "PFQ061M": "Difficulty standing for long periods",
        "PFQ061N": "Difficulty sitting for long periods",
        "PFQ061O": "Difficulty reaching up",
        "PFQ061P": "Difficulty grasping small objects",
        "PFQ061Q": "Difficulty going out to movies or events",
        "PFQ061R": "Difficulty attending social events",
        "PFQ061S": "Difficulty with home leisure activities",
        "PFQ061T": "Difficulty pushing pulling large objects",
        "PFQ063A": "Difficulty due to health problems",
        "PFQ063B": "Difficulty due to health problems",
        "PFQ063C": "Difficulty due to health problems",
        "PFQ063D": "Difficulty due to health problems",
        "PFQ063E": "Difficulty due to health problems",
        "RXD295": "Medications taken (number)",
        "RXDCOUNT": "Medications taken (number)",
        "RDQ030": "Respiratory Cough most days",
        "RDQ050": "Respiratory Bring up phlegm",
        "RDQ070": "Respiratory Wheezing in chest",
        "RDQ135": "Respiratory Limited activity",
        "RDQ140": "Respiratory Dry cough at night",
        "RDD030": "Respiratory Cough most days",
        "RDQ031": "Respiratory Cough most days",
        "SLD010H": "Sleep duration (weekday, hours)",
        "SLD020M": "Sleep latency (minutes)",
        "SLQ030": "How often snort",
        "SLQ040": "How often stop breathing",
        "SLQ050": "Sleep trouble",
        "SLQ060": "Sleep disorder",
        "SLQ070A": "Sleep disorder (Apnea)",
        "SLQ070B": "Sleep disorder (Insomnia)",
        "SLQ070C": "Sleep disorder (Restless legs)",
        "SLQ070D": "Sleep disorder (Other)",
        "SLQ080": "How often have trouble falling asleep",
        "SLQ090": "How often wake up during night",
        "SLQ100": "How often wake up too early in morning",
        "SLQ110": "How often feel unrested during the day",
        "SLQ120": "How often feel overly sleepy during day",
        "SLQ130": "How often did you not get enough sleep",
        "SLQ140": "How often take pills to help you sleep",
        "SLQ150": "How often have leg jerks while sleeping",
        "SLQ160": "How often have legs cramp while sleeping",
        "SLQ170": "Difficulty concentrating when tired",
        "SLQ180": "Difficulty remembering when tired",
        "SLQ190": "Difficulty eating when tired",
        "SLQ200": "Difficulty with a hobby when tired",
        "SLQ210": "Difficulty getting things done",
        "SLQ220": "Difficulty with finance when tired",
        "SLQ230": "Difficulty at work because tired",
        "SLQ240": "Difficulty on phone when tired",
        "SLQ300": "Sleep go-to-sleep time",
        "SLQ310": "Sleep wake-up time",
        "SLD012": "Sleep duration (weekday, hours)",
        "SLQ320": "Sleep go-to-sleep time (weekend)",
        "SLQ330": "Sleep wake-up time (weekend)",
        "SLD013": "Sleep duration (weekend, hours)",
        "SMQ020": "Smoking status",
        "SMD030": "Smoking regularly",
        "SMQ040": "Smoking now",
        "SMQ120": "Smoking status",
        "SMD130": "Smoking regularly",
        "SMQ140": "Smoking now",
        "SMQ150": "Smoking status",
        "SMD160": "Smoking regularly",
        "SMQ170": "Smoking now",
        "TBQ040": "Tuberculosis",
        "VIQ030": "Vision",
        "VIQ050A": "Vision Difficulty reading ordinary newsprint",
        "VIQ050B": "Vision Difficulty with up close work or chores",
        "VIQ050C": "Vision Difficulty seeing steps in dim light",
        "VIQ050D": "Vision Difficulty noticing objects to side",
        "VIQ050E": "Vision Difficulty findng object on crowdedshelf",
        "VIQ055": "Vision Difficulty drivng daytime-familiar place",
        "VIQ070": "Vision Cataract",
        "VIQ031": "Vision",
        "VIQ051A": "Vision Difficulty reading ordinary newsprint",
        "VIQ051B": "Vision Difficulty with up close work or chores",
        "VIQ051C": "Vision Difficulty seeing steps in dim light",
        "VIQ051D": "Vision Difficulty noticing objects to side",
        "VIQ051E": "Vision Difficulty findng object on crowdedshelf",
        "VIQ056": "Vision Difficulty drivng daytime-familiar place",
        "VIQ071": "Vision Cataract",
        "VIQ017": "Vision Blind",
        "VIQ090": "Vision Glaucoma",
        "VIQ310": "Vision Macular degeneration",
        "BPQ150A": "Blood pressure food",
        "BPQ150B": "Blood pressure alcohol",
        "BPQ150C": "Blood pressure coffee",
        "BPQ150D": "Blood pressure cigarettes",
        "BPXPLS": "Pulse rate (bpm)",
        "BPXSY1": "Blood pressure systolic (mm Hg)",
        "BPXDI1": "Blood pressure diastolic (mm Hg)",
        "BPXSY2": "Blood pressure systolic (mm Hg)",
        "BPXDI2": "Blood pressure diastolic (mm Hg)",
        "BPXSY3": "Blood pressure systolic (mm Hg)",
        "BPXDI3": "Blood pressure diastolic (mm Hg)",
        "BPXSY4": "Blood pressure systolic (mm Hg)",
        "BPXDI4": "Blood pressure diastolic (mm Hg)",
        "BMXWT": "Weight (kg)",
        "BMXHT": "Height (cm)",
        "BMXBMI": "BMI (kg/m2)",
        "BMXLEG": "Upper leg length (cm)",
        "BMXARML": "Upper arm length (cm)",
        "BMXWAIST": "Waist circumference (cm)",
        "BMXTHICR": "Thigh Circumference (cm)",
        "MGATHAND": "Grip strength 1st hand (right/left)",
        "MGXH1T1": "Grip strength 1st hand (kg)",
        "MGXH2T1": "Grip strength 2nd hand (kg)",
        "MGXH1T2": "Grip strength 1st hand (kg)",
        "MGXH2T2": "Grip strength 2nd hand (kg)",
        "MGXH1T3": "Grip strength 1st hand (kg)",
        "MGXH2T3": "Grip strength 2nd hand (kg)",
        "LBDAPBSI": "Apolipoprotein B (g/L)",
        "LBXCRP": "CRP (C-reactive protein, mg/dL)",
        "LBDTRSI": "Triglycerides (mmol/L)",
        "LBDLDLSI": "LDL-cholesterol (mmol/L)",
        "LBDTCSI": "Cholesterol total (mmol/L)",
        "LBDHDLSI": "HDL-cholesterol (mmol/L)",
        "LB2TCSI": "Cholesterol total (mmol/L)",
        "LB2HDLSI": "HDL-cholesterol (mmol/L)",
        "LB2TRSI": "Triglycerides (mmol/L)",
        "LB2LDLSI": "LDL-cholesterol (mmol/L)",
        "LBXWBCSI": "WBC (White blood cell, 1000 cells/uL)",
        "LBXLYPCT": "Lymphocyte percentage (%)",
        "LBXMOPCT": "Monocyte percentage (%)",
        "LBXNEPCT": "Neutrophill percentage (%)",
        "LBXEOPCT": "Eosinophill percentage (%)",
        "LBXBAPCT": "Basophill percentage (%)",
        "LBDLYMNO": "Lymphocyte count (1000 cells/uL)",
        "LBDMONO": "Monocyte count (1000 cells/uL)",
        "LBDNENO": "Neutrophill count (1000 cells/uL)",
        "LBDEONO": "Eosinophill count (1000 cells/uL)",
        "LBDBANO": "Basophill count (1000 cells/uL)",
        "LBXRBCSI": "RBC (Red blood cell, M cells/uL)",
        "LBXHGB": "Hemoglobin (g/dL)",
        "LBXMCVSI": "MCV (Mean cell volume, fL)",
        "LBXMCHSI": "MCH (Mean cell hemoglobin, pg)",
        "LBXMC": "MCHC (Mean cell hemoglob conc, g/dL)",
        "LBXRDW": "RDW (Red cell distrib width, %)",
        "LBXPLTSI": "Platelet count (1000 cells/uL)",
        "LBXMPSI": "Mean platelet volume (fL)",
        "LBXHCT": "Hematocrit (%)",
        "LBXGH": "Glycohemoglobin (HbA1c, %)",
        "LBDSALSI": "Albumin (g/L)",
        "LBXSATSI": "ALT (Alanine aminotransferase, U/L)",
        "LBXSASSI": "AST (Aspartate aminotransferase, U/L)",
        "LBXSAPSI": "ALP (Alkaline phosphatase, IU/L)",
        "LBDSBUSI": "Blood Urea Nitrogen (mmol/L)",
        "LBDSCASI": "Calcium total (mmol/L)",
        "LBDSCHSI": "Cholesterol (mmol/L)",
        "LBXSC3SI": "Bicarbonate (mmol/L)",
        "LBXSGTSI": "GGT (Gamma Glutamyl Transferase, U/L)",
        "LBDSGLSI": "Glucose, serum (mmol/L)",
        "LBDSIRSI": "Iron (umol/L)",
        "LBDSPHSI": "Phosphate (mmol/L)",
        "LBDSTBSI": "Bilirubin total (umol/L)",
        "LBDSTPSI": "Protein total (g/L)",
        "LBDSTRSI": "Triglycerides (mmol/L)",
        "LBDSUASI": "Urate (umol/L)",
        "LBDSCRSI": "Creatinine (umol/L)",
        "LBXSNASI": "Sodium (mmol/L)",
        "LBXSKSI": "Potassium (mmol/L)",
        "LBXSCLSI": "Chloride (mmol/L)",
        "LBXSOSSI": "Osmolality (mmol/Kg)",
        "LBDSGBSI": "Globulin (total) (g/L)",
        "LBXSCK": "CPK (Creatine Phosphokinase, U/L)",
        "LBXTST": "Testosterone (ng/dL)",
        "LBXEST": "Estradiol (pg/mL)",
        "LBXSHBG": "SHBG (nmol/L)",
        "DRQSDIET": "Food On a special diet",
        "DSD010": "Food Use supplements",
    }
    return dct


import types
__all__ = [name for name, thing in globals().items()
          if not (name.startswith('_') or isinstance(thing, types.ModuleType))]
del types
