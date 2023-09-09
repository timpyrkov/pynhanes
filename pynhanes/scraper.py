#!/usr/bin/env python
# -*- coding: utf8 -*-

import os
import requests
from bs4 import BeautifulSoup
import pandas as pd
from tqdm import tqdm
import json


class NhanesScraper:
    """
    Class to scrape and store variable codebooks from NHANES website
    https://wwwn.cdc.gov/nchs/nhanes/default.aspx

    Parameters
    ----------
    components : list or None, default None
        List of components, e.g. ["Demographics", "Questionnaire"]
    years : list or None, default None
        List of years, e.g. [2003, 2005]
    recodebins : bool, default True
        If True, recode {1 - Yes/Male, 2 - No/Female} to {0 - No/Female, 1 - Yes/Male}

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

    """

    def __init__(self, components=None, years=None, recodebins=True):
        survey_components = ["Demographics", "Questionnaire"]
        survey_components += ["Examination", "Laboratory", "Dietary"]
        survey_years = [2000 + i for i in range(-1,20)[::2]]
        self.survey_components = components if components is not None else survey_components
        self.survey_years = years if years is not None else survey_years
        self.survey_suffix = {y: f"_{chr(66 + (y - 2000) // 2)}" for y in self.survey_years}
        self.categ_dict = {}
        self.var_dict = {}
        for year in self.survey_years:
            for component in self.survey_components:
                self.scrape_doc_files(component, year)
        self.add_mortality_data()
        self.add_occupation_data()
        self.codebook_cleanup(recodebins)

        
    def codebook_cleanup(self, recodebins):
        """
        Add "Missing" values and "0" values for binary codes

        Parameters
        ----------
        recodebins : bool
            If True, recode binary labels {1: Yes, 2: No} -> {0: No, 1: Yes}

        """
        for var in self.var_dict:
            var_data = self.var_dict[var]
            var_name = var_data["name"].lower()
            codebook = var_data["codebook"]
            if recodebins:
                recode = False
                if var_name.find("gender") >= 0 and "0" not in codebook:
                    recode = True
                if "1" in codebook and "2" in codebook and "3" not in codebook and "0" not in codebook:
                    if codebook["1"].lower() == "yes" and codebook["2"].lower() == "no":
                        recode = True
                if recode:
                    codebook["0"] = codebook["2"]
                    del codebook["2"]
            # SORT CODEBOOK LABELS
            codebook = dict(sorted(codebook.items(), key=lambda x: x[0].lower()))
            codebook.pop(".", None)
            codebook.update({".": "Missing"})
            var_data["codebook"] = codebook
            self.var_dict[var] = var_data
        return


    def scrape_doc_files(self, component, year):
        """
        Scrape category list from component/year page on NHANES website

        Parameters
        ----------
        component : str
            Components, e.g. "Demographics"
        year : int
            Year, e.g. 2003

        """
        print(f"Scraping ({component}, {year})...")
        doc_urls = []
        url = "https://wwwn.cdc.gov/nchs/nhanes/search/datapage.aspx?" \
              "Component={}&CycleBeginYear={}".format(component, year)
        soup = BeautifulSoup(requests.get(url).content)
        rows = soup.find_all("tr")[1:]
        for row in rows:
            categ_name = row.find("td").text
            categ_code = row.find("a").text.replace(" Doc", "")
            categ_code = categ_code.removesuffix(self.survey_suffix[year])
            categ_url = "https://wwwn.cdc.gov/" + row.find("a")["href"]
            if categ_url != "#":
                doc_urls.append(categ_url)
                if categ_code not in self.categ_dict:
                    self.categ_dict[categ_code] = categ_name
        for doc_url in tqdm(doc_urls):
            try:
                self.scrape_variables(doc_url)
            except:
                pass
        return


    def scrape_variables(self, doc_url):
        """
        Scrape variable list from category page on NHANES website

        Parameters
        ----------
        doc_url : str
            Web address of category variables description

        """
        year = int(doc_url.split("/")[-2].split("-")[0])
        category = doc_url.split("/")[-1].split(".")[0]
        category = category.removesuffix(self.survey_suffix[year])
        
        soup = BeautifulSoup(requests.get(doc_url).content)
        variables = soup.find_all("div", attrs={"class": "pagebreak"})
        for variable in variables:
            if variable.find("table"):
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
                    codebook.update(self.var_dict[var_code]["codebook"])
                var_data = {"code": var_code,
                            "name": var_name,
                            "category": category,
                            "codebook": codebook}
                self.var_dict[var_code] = var_data
        return


    def add_mortality_data(self):
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
                "code": code,
                "name": code_dict[code][0],
                "category": "MORT",
                "codebook": code_dict[code][1]
            }
        return


    def add_occupation_data(self):
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
            dct = self.var_dict[code]
            self.var_dict[code] = {
                "code": code,
                "name": dct["name"],
                "category": dct["category"],
                "codebook": code_dict,
            }
        return

    @property
    def varlist(self):
        """
        Get list of all variables
        
        Returns
        -------
        list
            List of all data variables

        """
        return list(self.var_dict.keys())


    @property
    def categlist(self):
        """
        Get list of all categories
        
        Returns
        -------
        list
            List of all data categories

        """
        return list(self.categ_dict.keys())


    def varlist_by_keyword(self, keyword, casesensitive=False):
        """
        Get list of variables containing a keyword
        
        Parameters
        ----------
        keyword : str
            Keyword
        casesensitive : bool, default False
            Flag to search case sensitive keyword

        Returns
        -------
        list
            List of data variables containing a keyword

        """
        variables = []
        for var in self.var_dict:
            var_name = self.var_dict[var]["name"]
            if casesensitive and  var_name.find(keyword) >= 0:
                variables.append(var)
            elif var_name.lower().find(keyword.lower()) >= 0:
                variables.append(var)
        return variables


    def varlist_by_category(self, category):
        """
        Get list of variables contained in a category
        
        Returns
        -------
        list
            List of data variables containing a keyword

        """
        variables = []
        for var in self.var_dict:
            var_categ = self.var_dict[var]["category"]
            if var_categ == category:
                variables.append(var)
        return variables
    
    def to_pandas(self):
        """
        Get codebook as pandas Dataframe
        
        Returns
        -------
        Dataframe
            Codebook dataframe

        """
        df = pd.DataFrame(self.var_dict).T
        df.sort_values(by=["category"], inplace=True)
        df["category_name"] = df["category"].map(self.categ_dict).values
        df["codebook"] = [json.dumps(c) for c in df["codebook"].values]
        df.set_index("code", inplace=True)
        df = df[["name", "category", "category_name", "codebook"]]
        return df

        
    def to_csv(self, path, sep=","):
        """
        Save codebook to .csv
        
        Parameters
        ----------
        path : str
            Path to output .csv
        sep : str, default ","
            Separate symbol for output .csv

        Returns
        -------
        Dataframe
            Codebook dataframe

        """
        df = self.to_pandas()
        path = os.path.expanduser(path)
        df.to_csv(path, sep=sep)
        return df
    


