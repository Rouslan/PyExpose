<?xml version="1.0"?>
<xsl:stylesheet version="1.0"
        xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
        xmlns:xsd="http://www.w3.org/2001/XMLSchema"
        xmlns:str="http://exslt.org/strings"
        extension-element-prefixes="str">
    <xsl:output indent="no" method="text"/>

    <xsl:template match="/">
        <xsl:text>
==================================
PyExpose Specification File Format
==================================

</xsl:text>
        <xsl:apply-templates select="xsd:schema/xsd:annotation/xsd:documentation"/>
        <xsl:text>
</xsl:text>

        <xsl:for-each select="//xsd:element[@name]">
            <xsl:text>* `</xsl:text>
            <xsl:value-of select="@name"/>
            <xsl:text>`_
</xsl:text>

        </xsl:for-each>
        <xsl:apply-templates select="//xsd:element[@name]"/>

    </xsl:template>

    <xsl:template name="datatype">
        <xsl:param name="type"/>
        <xsl:choose>
            <xsl:when test="$type='symbol'">"&lt;C++ symbol&gt;"</xsl:when>
            <xsl:when test="$type='arglist'">"&lt;argument list&gt;"</xsl:when>
            <xsl:when test="$type='retsemantictype'">"&lt;return semantic&gt;"</xsl:when>
            <xsl:when test="$type='ident'">"&lt;Python identifier&gt;"</xsl:when>
            <xsl:when test="$type='includelist'">"&lt;include list&gt;"</xsl:when>
            <xsl:when test="$type='fieldlist'">"&lt;field list&gt;"</xsl:when>
            <xsl:when test="$type='xsd:boolean'">"&lt;true/false&gt;"</xsl:when>
            <xsl:when test="$type='xsd:nonNegativeInteger'">"&lt;non-negative integer&gt;"</xsl:when>
            <xsl:when test="$type='expression'">"&lt;C++ expression&gt;"</xsl:when>
        </xsl:choose>
    </xsl:template>

    <xsl:template match="xsd:element[@name]">
        <xsl:text>

</xsl:text>
        <xsl:value-of select="@name"/>
        <xsl:text>
====================================

</xsl:text>
        <xsl:apply-templates select="xsd:annotation/xsd:documentation"/>
        <xsl:choose>
            <xsl:when test="@type">
                <xsl:variable name="type" select="@type"/>
                <xsl:apply-templates select="//xsd:complexType[@name=$type]"/>
            </xsl:when>
            <xsl:otherwise>
              <xsl:apply-templates select="xsd:complexType"/>
            </xsl:otherwise>
        </xsl:choose>
    </xsl:template>

    <xsl:template match="xsd:complexType">
        <xsl:if test="*/xsd:element">
            <xsl:text>
Child elements:
-----------------------

</xsl:text>
            <xsl:for-each select="*/xsd:element">
                <xsl:variable name="name">
                    <xsl:choose>
                        <xsl:when test="@ref">
                            <xsl:value-of select="@ref"/>
                        </xsl:when>
                        <xsl:otherwise>
                            <xsl:value-of select="@name"/>
                        </xsl:otherwise>
                    </xsl:choose>
                </xsl:variable>
                <xsl:if test="position() > 1">
                    <xsl:text>, </xsl:text>
                </xsl:if>
                <xsl:text>`</xsl:text>
                <xsl:value-of select="$name"/>
                <xsl:text>`_</xsl:text>
            </xsl:for-each>
            <xsl:text>

</xsl:text>
        </xsl:if>
        <xsl:if test="xsd:attribute">
            <xsl:variable name="element" select="@name"/>
                <xsl:text>
Attributes:
-----------

</xsl:text>
            <xsl:for-each select="xsd:attribute">
                <xsl:value-of select="@name"/>
                <xsl:text> = </xsl:text>
                <xsl:choose>
                    <xsl:when test="$element='var' and @name='ref'">
                        <xsl:text>"&lt;true/false/copy/managedptr/unmanagedref&gt;"</xsl:text>
                    </xsl:when>
                    <xsl:otherwise>
                        <xsl:call-template name="datatype">
                            <xsl:with-param name="type" select="@type"/>
                        </xsl:call-template>
                    </xsl:otherwise>
                </xsl:choose>
                <xsl:choose>
                    <xsl:when test="xsd:annotation/xsd:documentation">
                        <xsl:value-of select="str:replace(xsd:annotation/xsd:documentation/.,'&#xA;','&#xA;    ')"/>
                        <!-- <xsl:apply-templates select="xsd:annotation/xsd:documentation"/> -->
                    </xsl:when>
                    <xsl:otherwise>
                        <xsl:text>
    ..</xsl:text>
                    </xsl:otherwise>
                </xsl:choose>
                <xsl:text>
</xsl:text>
            </xsl:for-each>

        </xsl:if>
    </xsl:template>
</xsl:stylesheet>